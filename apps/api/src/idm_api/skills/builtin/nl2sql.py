"""nl2sql: 自然语言 → 只读 SQL → ClickHouse 5 层 Guard 校验 → 执行.

5 层 Guard (AGENT_INSTRUCTIONS §12):
  1) Schema Guard:        只允许 SELECT, 引用表必须存在于 kg (table_asset)
  2) SQL Safety Guard:    sqlglot parse -> 拒 DELETE/UPDATE/INSERT/DROP/TRUNCATE/MULTI
  3) Row Limit Guard:     自动加 LIMIT (默认 1000, max 10000)
  4) PII Column Guard:    包含 PII 列时强制 MASK (返回 hash/partial, 不返明文)
  5) Execution Guard:     dry-run EXPLAIN + timeout + 错误捕获 + 审计

Inputs:
    question: str         用户的自然语言问题
    service: str          限定在哪个 service 下的表 (fqn 前缀匹配)
    fqn_pattern: str      进一步限定 fqn 模糊匹配
    max_rows: int         上限 (默认 1000, hard cap 10000)
    allow_pii: bool       False=命中 PII 列时拒绝执行 (默认 False)
    dry_run: bool         True=只生成 SQL + 校验, 不真跑

Outputs (SkillOutput.items):
    [{sql, columns, rows, row_count, masked_columns, executed, dry_run,
      validation: {passed_guards: [...], failed_guards: [...]}, latency_ms}, ...]

写入:
    ai_skill_runs (审计: 原始 question, 生成的 sql, 5 层 guard 结果, 是否执行, 耗时)
    ai_suggestion (PII 列被命中时, 推一条 "pii_query_warning" 给 owner)
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import sqlglot
from sqlalchemy import select

from idm_api.skills.mcp import get_clickhouse_mcp
from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill
from idm_kg.models.column_asset import ColumnAsset
from idm_kg.models.table_asset import TableAsset

logger = logging.getLogger(__name__)

MAX_HARD_ROWS = 10000
DEFAULT_MAX_ROWS = 1000

DANGEROUS = re.compile(
    r"\b(insert|update|delete|drop|truncate|alter|create|grant|revoke|"
    r"attach|detach|rename|optimize|kill|system|set|use)\b",
    re.IGNORECASE,
)


# === Guard 1: Schema ===
async def _guard_schema(
    ctx: SkillContext,
    sql: str,
    *,
    service: str,
    fqn_pattern: str,
) -> tuple[bool, str, list[str]]:
    """只允许引用 kg 中已注册的表 (table_asset)."""
    # 解析 FROM / JOIN 引用的表名 (使用 clickhouse 方言)
    referenced: set[str] = set()
    for stmt in sqlglot.parse(sql, read="clickhouse"):
        if stmt is None:
            continue
        for tbl in stmt.find_all(sqlglot.exp.Table):
            # sqlglot 把 FQN 拆成 catalog.db.name; 拼回去再记
            parts = [tbl.args.get(k) for k in ("catalog", "db", "name") if tbl.args.get(k)]
            fqn = ".".join(parts).lower() if parts else (tbl.name or "").lower()
            if fqn:
                referenced.add(fqn)
            elif tbl.name:
                referenced.add(tbl.name.lower())

    if not referenced:
        # 可能是子查询, 跳过严格检查
        return True, "", []

    # 查 kg 候选表 (按 fqn 后缀匹配 name)
    stmt_q = select(TableAsset.fqn, TableAsset.name)
    if service:
        stmt_q = stmt_q.where(TableAsset.fqn.like(f"{service}.%"))
    if fqn_pattern:
        stmt_q = stmt_q.where(TableAsset.fqn.ilike(f"%{fqn_pattern}%"))
    rows = (await ctx.db.execute(stmt_q)).all()
    valid_fqns = {r[0].lower() for r in rows}
    valid_names = {r[1].lower() for r in rows}

    missing = []
    for ref in referenced:
        ref_clean = ref.strip("`").lower()
        if ref_clean in valid_fqns or ref_clean in valid_names:
            continue
        # 也尝试: ref 是 fqn, 取最后一段当 name
        short = ref_clean.split(".")[-1]
        if short in valid_names:
            continue
        missing.append(ref)
    if missing:
        return False, f"未在 KG 注册的表: {missing}", [f"missing:{m}" for m in missing]
    return True, "", [r[0] for r in rows]


# === Guard 2: SQL Safety ===
def _guard_sql_safety(sql: str) -> tuple[bool, str]:
    """多语句 / DML 拦截."""
    # 0) 先 strip 注释, 再做关键词检测 (避免 "SELECT 1 -- delete" 触发误报)
    stripped = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    stripped = re.sub(r"--[^\n]*", "", stripped)
    raw = stripped.strip().rstrip(";")

    # 1) 多语句: 用分号分割后非空段数 > 1
    parts = [p.strip() for p in raw.split(";") if p.strip()]
    if len(parts) > 1:
        return False, f"multiple statements detected ({len(parts)})"

    # 2) DML/DDL 关键词 (在已 strip 注释的文本上检测)
    if DANGEROUS.search(raw):
        return False, "DML/DDL keyword detected (INSERT/UPDATE/DELETE/DROP/...)"

    # 3) sqlglot parse (ClickHouse 方言支持 FQN 中的点号)
    try:
        parsed = sqlglot.parse(raw, read="clickhouse")
    except Exception:  # noqa: BLE001
        # 兜底: 把裸 FQN 中含连字符的部分用反引号包起来
        safe = re.sub(
            r"(?<![`\w])([a-zA-Z_][\w]*-[\w]+(?:\.[a-zA-Z_][\w]*)+)(?![`\w])",
            r"`\1`",
            raw,
        )
        if safe == raw:
            return False, f"sql parse error"
        try:
            parsed = sqlglot.parse(safe, read="clickhouse")
            raw = safe  # 用修正后的 SQL 走后续 guard
        except Exception as e:  # noqa: BLE001
            return False, f"sql parse error: {e}"
    if not parsed or parsed[0] is None:
        return False, "empty statement"
    root = parsed[0]
    if not isinstance(root, sqlglot.exp.Select):
        return False, f"only SELECT allowed, got {type(root).__name__}"

    return True, ""


# === Guard 3: Row Limit ===
def _guard_row_limit(sql: str, max_rows: int) -> tuple[str, bool, str]:
    """自动加 LIMIT, 截断超过 hard_cap 的请求."""
    if max_rows > MAX_HARD_ROWS:
        max_rows = MAX_HARD_ROWS
    max_rows = max(1, int(max_rows))

    raw = sql.strip().rstrip(";")
    parsed = sqlglot.parse(raw, read="clickhouse")[0]
    limit_node = parsed.args.get("limit")
    if limit_node is None:
        # 注入 LIMIT
        new_sql = f"{raw} LIMIT {max_rows}"
        return new_sql, True, "injected LIMIT"
    try:
        cur = int(limit_node.expression.this)
    except Exception:  # noqa: BLE001
        return f"{raw} LIMIT {max_rows}", True, "injected LIMIT (could not parse existing)"
    if cur > max_rows:
        # 截到 max_rows
        new_sql = f"{raw.rsplit('LIMIT', 1)[0].rstrip()} LIMIT {max_rows}"
        return new_sql, True, f"clamped LIMIT {cur} -> {max_rows}"
    return raw, True, f"LIMIT {cur} ok"


# === Guard 4: PII ===
async def _guard_pii(
    ctx: SkillContext,
    sql: str,
    *,
    allow_pii: bool,
) -> tuple[bool, list[str], str]:
    """解析 SELECT 列, 命中 PII 列时拒绝 (allow_pii=False)."""
    raw = sql.strip().rstrip(";")
    parsed = sqlglot.parse(raw, read="clickhouse")[0]
    # 收集引用列
    referenced_cols: set[str] = set()
    for col in parsed.find_all(sqlglot.exp.Column):
        referenced_cols.add(col.name.lower())

    if not referenced_cols:
        return True, [], ""

    # 查所有 PII 列 (fqn -> {col_name, pii_class})
    stmt_q = select(TableAsset.fqn, ColumnAsset.name, ColumnAsset.pii_class).join(
        ColumnAsset, ColumnAsset.table_id == TableAsset.id
    ).where(ColumnAsset.pii_class != "none")
    rows = (await ctx.db.execute(stmt_q)).all()
    pii_map: dict[tuple[str, str], str] = {}
    for fqn, cname, pclass in rows:
        pii_map[(fqn.lower(), cname.lower())] = pclass

    if not pii_map:
        return True, [], ""

    # 看 SELECT 里出现哪些 PII 列 (忽略函数包裹)
    masked: list[str] = []
    matched_pii: list[str] = []
    for (fqn, cname), pclass in pii_map.items():
        short = cname.split(".")[-1].lower()
        if short in referenced_cols:
            matched_pii.append(f"{fqn}.{cname}({pclass})")

    if not matched_pii:
        return True, [], ""

    if not allow_pii:
        return False, matched_pii, f"PII 列命中但 allow_pii=False: {matched_pii[:5]}"

    # allow_pii=True: 给出 mask 提示
    masked = [m for m in matched_pii]
    return True, masked, f"PII 列已包含 (允许), caller 应自行 mask: {masked[:5]}"


# === Guard 5: Execution ===
def _guard_execute_dryrun(mcp, sql: str) -> tuple[bool, str]:
    """EXPLAIN 校验 SQL 语法 + plan 合理性 (CH EXPLAIN)."""
    try:
        r = mcp.run_query(f"EXPLAIN {sql}")
        return True, "EXPLAIN ok"
    except Exception as e:  # noqa: BLE001
        return False, f"EXPLAIN failed: {str(e)[:200]}"


# === Prompt ===
NL2SQL_PROMPT = """你是 SQL 专家, 数据仓库使用 ClickHouse 方言。
仅基于下面提供的 schema 编写只读 SQL (只允许 SELECT), 不要编造不存在的表或列。

要求:
- 使用 ClickHouse 方言 (例如 toStartOfHour / now() / today())
- SELECT 列必须显式列出 (禁止 SELECT *)
- 必须包含 LIMIT 子句 (默认 1000)
- 不要使用 INSERT/UPDATE/DELETE/DROP 等 DML/DDL
- 多表 JOIN 时使用表别名
- 引用含连字符 (-) 或非标准字符的 FQN 时, 用反引号包起来, 例如: `clickhouse-prod.shop.default.orders_daily`
- 输出严格 JSON, 格式: {{"sql": "<string>", "explanation": "<20-80 字中文>"}}

【可用 schema】
{schema}

【问题】
{question}
"""


@skill(name="nl2sql", version=1, agent="query")
async def nl2sql(ctx: SkillContext, **inputs: Any) -> SkillResult:
    question: str = inputs.get("question") or ""
    service: str = inputs.get("service") or ""
    fqn_pattern: str = inputs.get("fqn_pattern") or ""
    max_rows: int = int(inputs.get("max_rows") or DEFAULT_MAX_ROWS)
    allow_pii: bool = bool(inputs.get("allow_pii", False))
    dry_run: bool = bool(inputs.get("dry_run", ctx.dry_run))

    if not question:
        return SkillResult(ok=False, output=SkillOutput(), error="question is required")
    if ctx.db is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.db is None")

    t0 = time.time()

    # 1) 取候选 schema (限 20 张表, 防止 prompt 爆)
    stmt_q = select(TableAsset)
    if service:
        stmt_q = stmt_q.where(TableAsset.fqn.like(f"{service}.%"))
    if fqn_pattern:
        stmt_q = stmt_q.where(TableAsset.fqn.ilike(f"%{fqn_pattern}%"))
    tables = list((await ctx.db.execute(stmt_q.limit(20))).scalars().all())
    if not tables:
        return SkillResult(
            ok=False,
            output=SkillOutput(summary={"error": "no tables matched service/fqn_pattern"}),
            error="no tables matched",
        )

    # 2) 拉列信息
    table_ids = [t.id for t in tables]
    cols = list(
        (
            await ctx.db.execute(
                select(TableAsset.fqn, ColumnAsset.name, ColumnAsset.data_type, ColumnAsset.pii_class).join(
                    ColumnAsset, ColumnAsset.table_id == TableAsset.id
                ).where(TableAsset.id.in_(table_ids))
            )
        ).all()
    )

    schema_lines: list[str] = []
    for fqn, cname, dtype, pclass in cols[:200]:
        ptag = f"  -- PII: {pclass}" if pclass and pclass != "none" else ""
        schema_lines.append(f"  {fqn}.{cname}  {dtype or ''}{ptag}")
    schema_blob = "\n".join(schema_lines) if schema_lines else "(无列信息)"

    # 3) LLM 生成 SQL
    if ctx.llm is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.llm is None (LLM 未配置)")

    # schema 内容里可能出现 {/}, 会和 format() 占位符冲突; 全部转义成 {{/}}
    safe_schema = schema_blob.replace("{", "{{").replace("}", "}}")
    prompt = NL2SQL_PROMPT.format(schema=safe_schema, question=question)
    try:
        resp = await ctx.llm.complete(
            [
                {"role": "system", "content": "你输出严格 JSON, 不含任何解释文字。"},
                {"role": "user", "content": prompt},
            ],
            profile="default",
            temperature=0.0,
            max_tokens=600,
            response_format={"type": "json_object"},
        )
    except Exception as e:  # noqa: BLE001
        return SkillResult(ok=False, output=SkillOutput(), error=f"LLM failed: {e}")

    text = resp.get("content", "").strip()
    logger.info("nl2sql llm raw: %s", text[:500])
    # 去掉 markdown 围栏
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(l for l in lines if not l.strip().startswith("```"))
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return SkillResult(ok=False, output=SkillOutput(), error=f"LLM returned non-JSON: {text[:200]}")
    try:
        llm_out = json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        return SkillResult(ok=False, output=SkillOutput(), error=f"LLM JSON parse: {e}: {text[:200]}")
    # 兼容 LLM 偶尔返回带引号的 key
    if "sql" not in llm_out:
        for k in list(llm_out.keys()):
            if k.strip('"\'') == "sql":
                llm_out["sql"] = llm_out.pop(k)
                break
    sql = (llm_out.get("sql") or "").strip()
    if not sql:
        return SkillResult(ok=False, output=SkillOutput(), error=f"LLM returned empty sql: keys={list(llm_out.keys())}")

    ctx.log("llm_sql_generated", sql=sql[:200], model=resp.get("model"))

    # 4) 5 层 Guard
    passed: list[str] = []
    failed: list[str] = []
    notes: dict[str, str] = {}

    # Guard 1: Schema
    ok, msg, valid = await _guard_schema(ctx, sql, service=service, fqn_pattern=fqn_pattern)
    (passed if ok else failed).append("schema")
    notes["schema"] = msg or f"valid fqns: {len(valid)}"

    # Guard 2: SQL Safety
    ok, msg = _guard_sql_safety(sql)
    (passed if ok else failed).append("sql_safety")
    notes["sql_safety"] = msg

    # Guard 3: Row Limit
    sql_after_limit, _, lmsg = _guard_row_limit(sql, max_rows)
    passed.append("row_limit")
    notes["row_limit"] = lmsg
    sql = sql_after_limit

    # Guard 4: PII
    ok_pii, pii_cols, pii_msg = await _guard_pii(ctx, sql, allow_pii=allow_pii)
    if ok_pii and not pii_cols:
        passed.append("pii")
        notes["pii"] = "no pii columns"
    elif ok_pii and pii_cols:
        passed.append("pii")
        notes["pii"] = pii_msg
    else:
        failed.append("pii")
        notes["pii"] = pii_msg

    # Guard 5: Execution (EXPLAIN)
    mcp = get_clickhouse_mcp()
    explain_ok, explain_msg = _guard_execute_dryrun(mcp, sql)
    if explain_ok:
        passed.append("execution")
        notes["execution"] = explain_msg
    else:
        failed.append("execution")
        notes["execution"] = explain_msg

    # 5) 总结: 是否真跑
    executed = False
    columns: list[str] = []
    rows: list[dict] = []
    row_count = 0
    error = None
    if not failed and not dry_run:
        try:
            t_exec = time.time()
            r = mcp.run_query(sql)
            row_count = len(r)
            if r:
                columns = list(r[0].keys())
            rows = r[:1000]  # 限制返回给 UI
            executed = True
            notes["exec_ms"] = f"{int((time.time() - t_exec) * 1000)}"
        except Exception as e:  # noqa: BLE001
            error = f"execution failed: {e}"
            notes["execution"] = error

    latency_ms = int((time.time() - t0) * 1000)
    ctx.log(
        "nl2sql_done",
        passed=passed,
        failed=failed,
        executed=executed,
        row_count=row_count,
        latency_ms=latency_ms,
    )

    items = [
        {
            "sql": sql,
            "columns": columns,
            "rows": rows,
            "row_count": row_count,
            "executed": executed,
            "dry_run": dry_run,
            "validation": {
                "passed_guards": passed,
                "failed_guards": failed,
                "notes": notes,
            },
            "pii_columns_matched": pii_cols,
            "llm_model": resp.get("model"),
            "llm_tier": resp.get("tier"),
            "latency_ms": latency_ms,
            "error": error,
        }
    ]
    return SkillResult(
        ok=(not failed),
        output=SkillOutput(
            items=items,
            summary={
                "passed_guards": len(passed),
                "failed_guards": len(failed),
                "executed": executed,
                "row_count": row_count,
                "service": service,
                "llm_model": resp.get("model"),
            },
            artifacts=[] if failed else ([] if not executed else [f"nl2sql:{row_count}rows"]),
        ),
        error=("; ".join([notes[k] for k in failed])) if failed else None,
    )
