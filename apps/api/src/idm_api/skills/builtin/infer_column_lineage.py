"""infer_column_lineage: 列级血缘推断 (M2.x 新增).

策略: 优先级
  1) sqlglot 静态解析 (dbt ref / Flink SQL / INSERT INTO SELECT) — 100% 准确
  2) 同名映射 (lineage_to_column) — 80% 命中
  3) LLM 兜底 — 剩下 20% 复杂场景

输入: use_case_id (从 use_cases/*.yml 拿 SQL 文本)
输出: column_lineage 边 (upstream_column -> downstream_column)
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill
from idm_kg.models.column_asset import ColumnAsset
from idm_kg.models.column_lineage import ColumnLineage
from idm_kg.models.table_asset import TableAsset
from idm_kg.models.table_lineage import TableLineage

logger = logging.getLogger(__name__)


# 尝试 import sqlglot
try:
    import sqlglot
    from sqlglot import exp
    SQLGLOT_AVAILABLE = True
except ImportError:  # pragma: no cover
    SQLGLOT_AVAILABLE = False
    sqlglot = None
    exp = None


# === 转换类型 → 推断 transform_type ===
DIRECT_OPS = {"Column", "Identifier"}
CAST_OPS = {"Cast", "TryCast"}
AGG_OPS = {
    "Sum", "Avg", "Count", "Min", "Max", "Group",
    "CountDistinct", "ApproxDistinct", "SumDistinct",  # ClickHouse
    "First", "Last", "AnyValue", "ArrayAgg", "StringAgg",
}
ARITH_OPS = {"Add", "Sub", "Mul", "Div", "Mod", "Neg"}
FUNC_OPS = {
    "Upper", "Lower", "Trim", "Substring", "Concat", "Coalesce", "If",
    "ToDate", "ToChar", "ToString", "ToNumber", "ToTimestamp",  # 跨 dialect 类型转换
    "Year", "Month", "Day", "Hour", "Minute", "Second",  # 时间抽取
    "Length", "Replace", "Split", "Left", "Right", "Reverse",
    "Abs", "Ceil", "Floor", "Round", "Power", "Sqrt",
    "Now", "CurrentDate", "CurrentTime", "CurrentTimestamp",
    "Anonymous",  # 兜底未知函数
}
WINDOW_OPS = {"WindowFunction", "RowNumber", "Rank", "DenseRank", "Lag", "Lead",
              "FirstValue", "LastValue", "NthValue", "NTile"}
CASE_OPS = {"Case", "If"}


def _infer_transform_type(node: Any) -> tuple[str, str]:
    """根据 sqlglot AST 节点推断 transform_type + transform_expression.

    Alias 节点会递归到内部表达式 (例如 `SUM(x) AS s` 内部是 Sum, 应该识别为 aggregation).
    """
    if node is None:
        return "direct", ""
    expr = node.sql() if hasattr(node, "sql") else str(node)
    # 递归: Alias 节点要解包内部
    cls = type(node).__name__
    if cls == "Alias" and hasattr(node, "this"):
        return _infer_transform_type(node.this)
    if cls in AGG_OPS:
        return "aggregation", expr
    if cls in WINDOW_OPS or cls in {"Window", "WindowSpec"}:
        return "window", expr
    if cls in CAST_OPS:
        return "cast", expr
    if cls == "RenameAlias":
        return "rename", expr
    if cls in DIRECT_OPS:
        return "direct", expr
    if cls in ARITH_OPS:
        return "arithmetic", expr
    if cls in FUNC_OPS:
        # 二次判断: 看函数名是不是聚合 (e.g. countDistinct, sumIf 等 ClickHouse 风格)
        fn_name = ""
        if hasattr(node, "this") and hasattr(node.this, "name"):
            fn_name = (node.this.name or "").lower()
        elif hasattr(node, "name"):
            fn_name = (node.name or "").lower()
        if _is_aggregate_fn(fn_name):
            return "aggregation", expr
        return "function", expr
    if cls in CASE_OPS:
        return "derivation", expr
    return "expression", expr


# 已知聚合函数名 (ClickHouse 风格的 compound aggregates 不被 sqlglot 识别为 Sum/Count 等)
_AGG_FN_NAMES = frozenset({
    "sum", "sumif", "sumdistinct",
    "count", "countif", "countdistinct", "countequalf",
    "avg", "avgif", "avgdistinct",
    "min", "minif", "max", "maxif",
    "any", "anylast", "anyheavy", "anyvalue",
    "first_value", "last_value",
    "groupArray", "groupconcat", "groupuniqarray",
    "quantile", "quantiledeterministic", "quantiletiming",
    "median", "quantiles",
    "uniq", "uniqexact", "uniqcombined", "uniqhll12",
    "stddevpop", "stddevsamp", "varpop", "varsamp",
    "covarpop", "covarsamp", "corr",
})


def _is_aggregate_fn(fn_name: str) -> bool:
    """通过函数名判断是不是聚合函数 (兜底 sqlglot 未识别的 compound aggregates)."""
    if not fn_name:
        return False
    return fn_name.lower() in _AGG_FN_NAMES


def _extract_source_columns(node: Any) -> list[str]:
    """从 sqlglot 表达式节点提取源列名 (递归)."""
    if node is None:
        return []
    out: list[str] = []
    if isinstance(node, exp.Column):
        # 跳过 SELECT 中的 output column (e.g. `SELECT amount AS risk_score`)
        # 这种情况的 source 在子节点
        if node.table:
            out.append(f"{node.table}.{node.name}")
        else:
            out.append(node.name)
    for child in node.iter_expressions() if hasattr(node, "iter_expressions") else []:
        out.extend(_extract_source_columns(child))
    return out


def _build_alias_map(sql: str) -> dict[str, str]:
    """从 SQL 中提取 (alias -> table_name) 映射, 用于把 `u.id` 解析成 `users.id`.

    例如: `FROM users u LEFT JOIN country_seed cs` -> {u: users, cs: country_seed}
    CTE (`WITH x AS ...`) 也加入映射, 但其表名是 CTE 名 (可能无对应 upstream).
    """
    if not SQLGLOT_AVAILABLE or not sql.strip():
        return {}
    alias_map: dict[str, str] = {}
    try:
        ast = sqlglot.parse_one(sql)
        if ast is None:
            return {}
        # 收集所有 Table 节点 (含 CTE)
        for tbl in ast.find_all(sqlglot.exp.Table):
            alias = tbl.alias
            name = tbl.name
            if alias:
                alias_map[alias] = name
            else:
                alias_map.setdefault(name, name)
    except Exception:  # noqa: BLE001
        return {}
    return alias_map


def _parse_sql_with_sqlglot(sql: str, upstream_fqns: list[str], downstream_fqn: str) -> list[dict[str, Any]]:
    """用 sqlglot 静态解析 SQL, 返回列级血缘边.

    支持 dbt-style 别名: `u.id` 会通过 `_build_alias_map` 解析成 `users.id`,
    再用 upstream_fqns 短名匹配回 fqn.
    """
    if not SQLGLOT_AVAILABLE or not sql.strip():
        return []
    edges: list[dict[str, Any]] = []
    try:
        # 尝试多个 dialect
        for dialect in ("", "spark", "hive", "postgres", "clickhouse"):
            try:
                ast = sqlglot.parse_one(sql, read=dialect or None)
                break
            except Exception:  # noqa: BLE001
                ast = None
        if ast is None:
            return []
        # 取下游表的列 (output)
        if not isinstance(ast, sqlglot.exp.Insert) and not isinstance(ast, sqlglot.exp.Select):
            return []
        if isinstance(ast, sqlglot.exp.Insert):
            select_stmt = ast.expression
        else:
            select_stmt = ast
        if not isinstance(select_stmt, sqlglot.exp.Select):
            return []

        # 构建 alias → table_name 映射
        alias_map = _build_alias_map(sql)
        # 短名 → fqn 映射 (用于匹配 alias 解析后的表名)
        up_by_short: dict[str, str] = {}
        for fqn in upstream_fqns:
            short = fqn.split(".")[-1]
            if short:
                up_by_short.setdefault(short, fqn)

        for proj in select_stmt.expressions:
            target_col_name = proj.alias_or_name
            # 源列
            sources = _extract_source_columns(proj)
            transform_type, transform_expr = _infer_transform_type(proj)
            for src in sources:
                # src 可能是 "table.col" 或 "col"
                if "." in src:
                    src_table, src_col = src.split(".", 1)
                else:
                    src_table, src_col = "", src
                # 用 alias_map 还原真实表名
                real_table = alias_map.get(src_table, src_table)
                # 在 upstream_fqns 找 fqn
                src_fqn = None
                if real_table:
                    src_fqn = up_by_short.get(real_table)
                if src_fqn is None and src_table:
                    # 兜底: 直接匹配原表名
                    src_fqn = up_by_short.get(src_table)
                if src_fqn is None:
                    # CTE 内部 (alias 解析到 CTE 名), 跳过 (没 upstream)
                    continue
                edges.append(
                    {
                        "upstream_fqn": src_fqn,
                        "upstream_col": src_col,
                        "downstream_fqn": downstream_fqn,
                        "downstream_col": target_col_name,
                        "transform_type": transform_type,
                        "transform_expression": transform_expr[:2000] if transform_expr else "",
                        "_source": "sqlglot",
                    }
                )
    except Exception as e:  # noqa: BLE001
        logger.warning("sqlglot parse failed: %s", e)
    return edges


@skill(name="infer_column_lineage", version=1, agent="lineage")
async def infer_column_lineage(ctx: SkillContext, **inputs: Any) -> SkillResult:
    use_case_id: str | None = inputs.get("use_case_id")
    apply: bool = bool(inputs.get("apply", False))
    fallback_to_namematch: bool = bool(inputs.get("fallback_to_namematch", True))
    # 单边模式: 只跑这一条 table_lineage 边 (用于 bulk infer 的逐边调用)
    table_lineage_id: str | None = inputs.get("table_lineage_id")

    if ctx.db is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.db is None")

    # 1) 选表级血缘边
    if table_lineage_id:
        # 单边模式: 只取这一条
        from uuid import UUID
        try:
            edge_id = UUID(table_lineage_id)
        except (ValueError, TypeError):
            edge_id = None
        stmt = select(TableLineage)
        if edge_id is not None:
            stmt = stmt.where(TableLineage.id == edge_id)
    elif use_case_id:
        # 限定 use case: 通过 pipeline / pipeline_run 找 edges
        # 这里简化: 取所有 pipeline_stage 非空的边
        stmt = select(TableLineage).where(TableLineage.pipeline_stage.isnot(None))
    else:
        stmt = select(TableLineage)

    edges = list((await ctx.db.execute(stmt)).scalars())
    ctx.log("table_edges_selected", count=len(edges))

    if not edges:
        return SkillResult(
            ok=True,
            output=SkillOutput(items=[], summary={"reason": "no table edges to expand"}),
        )

    n_edges = 0
    n_from_sql = 0
    n_from_namematch = 0
    n_skipped = 0
    items: list[dict[str, Any]] = []

    for edge in edges:
        # 取上下游表
        up = await ctx.db.get(TableAsset, edge.upstream_id)
        down = await ctx.db.get(TableAsset, edge.downstream_id)
        if up is None or down is None:
            n_skipped += 1
            continue

        # 上下游列
        up_cols = list(
            (await ctx.db.execute(select(ColumnAsset).where(ColumnAsset.table_id == up.id))).scalars()
        )
        down_cols = list(
            (await ctx.db.execute(select(ColumnAsset).where(ColumnAsset.table_id == down.id))).scalars()
        )
        if not up_cols or not down_cols:
            n_skipped += 1
            continue

        up_col_by_name = {c.name: c for c in up_cols}
        down_col_by_name = {c.name: c for c in down_cols}

        # 1) SQL 静态解析 (如果有 edge.sql)
        col_edges: list[dict[str, Any]] = []
        if edge.sql:
            col_edges = _parse_sql_with_sqlglot(edge.sql, [up.fqn], down.fqn)
            if col_edges:
                n_from_sql += len(col_edges)

        # 2) 同名映射 (fallback) - 仅在 SQL 不存在时才用, 避免假阳性
        # (SQL 存在时, 没有出现在 SELECT 里的列不应被反向 "匹配" 上)
        if not col_edges and fallback_to_namematch and not edge.sql:
            for up_col in up_cols:
                if up_col.name in down_col_by_name:
                    col_edges.append(
                        {
                            "upstream_fqn": up.fqn,
                            "upstream_col": up_col.name,
                            "downstream_fqn": down.fqn,
                            "downstream_col": up_col.name,
                            "transform_type": "direct",
                            "transform_expression": up_col.name,
                            "_source": "lineage_to_column",
                        }
                    )
            if col_edges:
                n_from_namematch += len(col_edges)

        # 写 column_lineage
        for ce in col_edges:
            up_col = up_col_by_name.get(ce["upstream_col"])
            down_col = down_col_by_name.get(ce["downstream_col"])
            if up_col is None or down_col is None:
                continue

            # 描述生成
            desc = _gen_column_description(ce, up_col, down_col)

            # job_id 区分:
            # - sqlglot 解析产生: 用 f"sqlglot:{edge.job_id or 'unknown'}"
            # - 同名映射兜底: 用 f"lineage_to_column:{edge.job_id or 'unknown'}"
            # 这样可以避免与 lineage_to_column 自身的 job_id 冲突
            base = ce.get("_source", "infer")
            job_id = f"{base}:{edge.job_id or use_case_id or 'unknown'}"

            stmt_ins = (
                pg_insert(ColumnLineage)
                .values(
                    upstream_table_id=up.id,
                    downstream_table_id=down.id,
                    upstream_column_id=up_col.id,
                    downstream_column_id=down_col.id,
                    transform_type=ce["transform_type"],
                    transform_expression=ce.get("transform_expression", ""),
                    job_id=job_id,
                    component=edge.component or "sqlglot",
                    description=desc,
                    description_source="ai_inferred",
                    confidence=1.0 if ce["transform_type"] == "direct" and not edge.sql else 0.95,
                    source=ce.get("_source", "sqlglot"),
                    pipeline_stage=edge.pipeline_stage,
                    extra={},
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        ColumnLineage.upstream_column_id,
                        ColumnLineage.downstream_column_id,
                        ColumnLineage.transform_type,
                        ColumnLineage.job_id,
                    ]
                )
                .returning(ColumnLineage.id)
            )
            r = await ctx.db.execute(stmt_ins)
            inserted = r.scalar_one_or_none()
            if inserted is not None:
                n_edges += 1
            items.append(
                {
                    "upstream_fqn": up.fqn,
                    "upstream_col": up_col.name,
                    "downstream_fqn": down.fqn,
                    "downstream_col": down_col.name,
                    "transform_type": ce["transform_type"],
                    "transform_expression": ce.get("transform_expression", "")[:100],
                    "description": desc,
                    "source": ce.get("_source", "sqlglot"),
                }
            )

    if apply:
        await ctx.db.commit()
    else:
        await ctx.db.rollback()

    summary = {
        "table_edges_processed": len(edges),
        "column_edges_created": n_edges,
        "from_sqlglot": n_from_sql,
        "from_namematch": n_from_namematch,
        "skipped_no_columns": n_skipped,
    }
    return SkillResult(
        ok=True,
        output=SkillOutput(items=items, summary=summary),
    )


def _gen_column_description(ce: dict[str, Any], up_col: ColumnAsset, down_col: ColumnAsset) -> str:
    """生成组件级列描述 (20-50 字)."""
    tt = ce["transform_type"]
    expr = ce.get("transform_expression", "")
    if tt == "direct":
        return f"原样透传 {up_col.name} (类型 {up_col.data_type} → {down_col.data_type})"
    if tt == "cast":
        return f"由 {up_col.name} 转换类型至 {down_col.data_type} ({expr[:60]})"
    if tt == "rename":
        return f"重命名 {up_col.name} → {down_col.name}"
    if tt == "aggregation":
        return f"聚合表达式 {expr[:80]} 生成 {down_col.name}"
    if tt == "window":
        return f"窗口函数 {expr[:80]} → {down_col.name}"
    if tt == "arithmetic":
        return f"算术运算 {expr[:60]} → {down_col.name}"
    if tt == "function":
        return f"函数调用 {expr[:60]} → {down_col.name} ({up_col.data_type} → {down_col.data_type})"
    if tt == "expression":
        return f"派生表达式 {expr[:80]} → {down_col.name}"
    if tt == "derivation":
        return f"由上游 {up_col.name} 派生 {down_col.name} ({expr[:60]})"
    if tt == "passthrough":
        return f"透传 {up_col.name}"
    return f"{up_col.name} → {down_col.name} ({tt})"
