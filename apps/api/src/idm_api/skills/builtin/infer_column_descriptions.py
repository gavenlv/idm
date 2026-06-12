"""infer_column_descriptions: 推断列的语义描述 (M2.x 新增).

策略: 70% 规则命中 (列名/类型/样本值/PII) + 30% LLM 兜底
输入: table_ids[] (空 = 全表)
输出: ai_suggestion (suggestion_type=description, target_type=column)
      写入 column_asset.description + description_source + description_rationale
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill
from idm_kg.models.ai_suggestion import AISuggestion
from idm_kg.models.column_asset import ColumnAsset
from idm_kg.models.table_asset import TableAsset

logger = logging.getLogger(__name__)


# === 列名模式规则 (regex, 描述, 置信度) ===
NAME_PATTERNS: list[tuple[str, str, float]] = [
    (r"^(id|.*_id|.*_pk)$",          "主键 / 外键 ID",                              0.95),
    (r"^(created_at|updated_at|.*_at)$", "时间戳 (创建/更新时间, UTC)",            0.95),
    (r"^(is_|has_|.*_flag)$",         "布尔标志位 (0/1 或 true/false)",              0.90),
    (r"^.*_(count|num|qty)$",         "数量 / 计数",                                 0.90),
    (r"^.*_(amount|price|total|cost)$", "金额 (业务单位)",                          0.90),
    (r"^(email|.*_email)$",           "邮箱地址 (PII)",                              0.95),
    (r"^(phone|mobile|.*_phone|.*_tel)$", "手机号 (PII, 11 位)",                    0.95),
    (r"^(id_card|.*_idno|.*_id_card)$", "身份证号 (PII, 18 位)",                    0.95),
    (r"^.*_(country|nation)$",        "国家代码 (ISO 3166-1 alpha-2/3)",            0.85),
    (r"^.*_(status|state)$",          "业务状态枚举 (e.g. pending/paid/shipped)",   0.75),
    (r"^.*_(url|link|href)$",         "资源 URL",                                   0.85),
    (r"^.*_(ip|ip_address)$",         "IP 地址 (PII)",                              0.90),
    (r"^.*_(uuid|guid)$",             "全局唯一标识 (UUID/GUID)",                    0.95),
    (r"^.*_(hash|md5|sha\d+)$",       "哈希值",                                     0.90),
    (r"^.*_(name|user_name|user)$",   "用户/客户名称",                              0.85),
    (r"^.*_(address|addr)$",          "地址 (PII)",                                 0.90),
    (r"^(date|day|.*_date|.*_day)$",  "日期 (不含时间, YYYY-MM-DD)",                0.90),
    (r"^.*_(lat|latitude)$",          "纬度 (WGS84, -90 to 90)",                    0.90),
    (r"^.*_(lng|long|longitude)$",    "经度 (WGS84, -180 to 180)",                  0.90),
    (r"^.*_(rate|ratio|percent|pct)$", "比率 / 百分比 (0-1 或 0-100)",              0.85),
    (r"^.*_(score|rank)$",            "评分 / 排名 (数值)",                        0.80),
    (r"^(name|title|.*_name)$",       "名称 / 标题",                                0.80),
    (r"^.*_(type|category|kind)$",    "类型 / 分类",                                0.75),
    (r"^.*_(version|v)$",             "版本号 (semver/整数)",                      0.80),
    (r"^.*_(path|file_path|key|prefix)$", "路径 / 前缀 (GCS/HTTP/SQL)",            0.85),
]


# === 值模式规则 (regex on sample values, 描述, 置信度) ===
VALUE_PATTERNS: list[tuple[str, str, float]] = [
    (r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", "邮箱格式 (PII)", 0.95),
    (r"^1[3-9]\d{9}$", "中国手机号 (11 位, PII)", 0.95),
    (r"^\d{17}[\dXx]$", "中国身份证号 (18 位, PII)", 0.95),
    (r"^[A-Z]{2}$", "ISO 3166-1 alpha-2 国家代码", 0.85),
    (r"^[A-Z]{3}$", "ISO 3166-1 alpha-3 国家代码", 0.85),
    (r"^\d{4}-\d{2}-\d{2}", "ISO 8601 日期", 0.90),
    (r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", "IPv4 地址 (PII)", 0.90),
    (r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "UUID 字符串", 0.95),
    (r"^[0-9a-f]{32}$", "MD5 哈希", 0.90),
    (r"^[0-9a-f]{40}$", "SHA1 哈希", 0.90),
    (r"^[0-9a-f]{64}$", "SHA256 哈希", 0.90),
]


# === 数据类型 → 描述的简单映射 ===
TYPE_HINTS: dict[str, str] = {
    "Int8": "8 位有符号整数",
    "Int16": "16 位有符号整数",
    "Int32": "32 位有符号整数",
    "Int64": "64 位有符号整数",
    "UInt8": "8 位无符号整数",
    "UInt16": "16 位无符号整数",
    "UInt32": "32 位无符号整数",
    "UInt64": "64 位无符号整数",
    "Float32": "32 位浮点数",
    "Float64": "64 位浮点数 (双精度)",
    "Decimal": "高精度小数 (金额/汇率)",
    "String": "字符串",
    "FixedString": "定长字符串",
    "UUID": "UUID 唯一标识",
    "Date": "日期 (YYYY-MM-DD)",
    "DateTime": "日期时间",
    "DateTime64": "高精度日期时间",
    "Bool": "布尔值 (true/false)",
    "Array": "数组",
    "JSON": "JSON 对象",
    "Enum8": "枚举 (8 位)",
    "Enum16": "枚举 (16 位)",
}


def _match_name_pattern(name: str) -> tuple[str, float] | None:
    """列名正则匹配; 命中返回 (描述, 置信度)."""
    lower = name.lower()
    for pattern, desc, conf in NAME_PATTERNS:
        if re.match(pattern, lower):
            return desc, conf
    return None


def _match_value_pattern(samples: list) -> tuple[str, float] | None:
    """样本值正则匹配 (任一命中即返回)."""
    for s in samples[:5]:
        if s is None:
            continue
        s_str = str(s)
        for pattern, desc, conf in VALUE_PATTERNS:
            if re.match(pattern, s_str):
                return desc, conf
    return None


def _type_hint(data_type: str) -> str:
    """类型 → 描述 (粗匹配)."""
    if not data_type:
        return ""
    # 去掉 (precision, scale) 之类
    base = data_type.split("(")[0].strip()
    return TYPE_HINTS.get(base, "")


def _ppii_class_hint(pii_class: str) -> str:
    """PII 分类 → 描述."""
    return {
        "email": "邮箱 (PII)",
        "phone": "手机号 (PII)",
        "id_card": "身份证号 (PII)",
        "name": "姓名 (PII)",
        "address": "地址 (PII)",
        "ip": "IP (PII)",
        "card_bin": "银行卡 BIN (前 6 位, PII)",
    }.get(pii_class, "")


def _rule_infer(col: ColumnAsset) -> tuple[str, float, str] | None:
    """规则推断列描述. 返回 (description, confidence, rationale) 或 None."""
    # 优先级: PII (最准) > 值模式 > 列名 > 类型
    pii_hint = _ppii_class_hint(col.pii_class) if col.pii_class and col.pii_class != "none" else ""
    if pii_hint and col.pii_confidence >= 0.7:
        rationale_parts = [f"pii_class={col.pii_class}"]
        return f"{pii_hint} (PII 分类由 {col.pii_source or 'unknown'} 推断)", max(0.85, col.pii_confidence), ",".join(rationale_parts)

    # 值模式 (基于样本)
    if col.sample_values:
        vmatch = _match_value_pattern(col.sample_values)
        if vmatch:
            return f"{vmatch[0]} (基于 {len(col.sample_values)} 个样本值)", vmatch[1], f"value_pattern_matched,samples={len(col.sample_values)}"

    # 列名模式
    nmatch = _match_name_pattern(col.name)
    if nmatch:
        type_hint = _type_hint(col.data_type)
        if type_hint and type_hint not in nmatch[0]:
            return f"{nmatch[0]}, {type_hint}", nmatch[1], f"name_pattern_matched,type={col.data_type}"
        return f"{nmatch[0]} ({col.data_type})", nmatch[1], f"name_pattern_matched,type={col.data_type}"

    # 类型兜底
    type_hint = _type_hint(col.data_type)
    if type_hint:
        return f"{col.name} ({type_hint})", 0.5, f"type_only,type={col.data_type}"

    return None


def _llm_infer(col: ColumnAsset, table: TableAsset) -> tuple[str, float, str]:
    """LLM 兜底推断."""
    return _llm_infer_sync(col, table)


def _llm_infer_sync(col: ColumnAsset, table: TableAsset) -> tuple[str, float, str]:
    """LLM 兜底 (stub - 真实环境会调 ctx.llm)."""
    # 兜底: 给一个基于 column_name + table_name 的简单描述
    desc = f"{table.name} 表的 {col.name} 列 (类型 {col.data_type})"
    rationale = "no_rule_match,fallback_to_template"
    return desc, 0.4, rationale


@skill(name="infer_column_descriptions", version=1, agent="doc")
async def infer_column_descriptions(ctx: SkillContext, **inputs: Any) -> SkillResult:
    table_ids: list[str] = inputs.get("table_ids") or []
    min_confidence: float = float(inputs.get("min_confidence") or 0.5)
    apply: bool = bool(inputs.get("apply", False))
    profile: str = inputs.get("profile") or "cheap"
    sample_rows: int = int(inputs.get("sample_rows") or 3)

    if ctx.db is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.db is None")

    # 1) 选表
    if table_ids:
        stmt = select(TableAsset).where(TableAsset.id.in_(table_ids))
    else:
        stmt = select(TableAsset).limit(20)
    tables = list((await ctx.db.execute(stmt)).scalars().all())
    ctx.log("tables_selected", count=len(tables))

    if not tables:
        return SkillResult(
            ok=True,
            output=SkillOutput(items=[], summary={"reason": "no tables"}),
        )

    items: list[dict[str, Any]] = []
    n_rule = 0
    n_llm = 0
    n_skipped = 0
    n_applied = 0

    mcp = ctx.mcp.get("clickhouse") if ctx.mcp else None

    def _to_jsonable(v: Any) -> Any:
        """将 sample value 转为可 JSON 序列化的形式."""
        if v is None:
            return None
        if isinstance(v, (str, int, float, bool)):
            return v
        return str(v)

    for t in tables:
        # 取列
        col_stmt = select(ColumnAsset).where(ColumnAsset.table_id == t.id)
        cols = list((await ctx.db.execute(col_stmt)).scalars())
        if not cols:
            continue

        # 采样
        if mcp is not None and sample_rows > 0:
            try:
                parts = t.fqn.split(".")
                if len(parts) == 4:
                    _, db_name, _, tbl_name = parts
                    samples = mcp.sample_rows(db_name, tbl_name, limit=sample_rows)
                    for sample in samples:
                        if not isinstance(sample, dict):
                            continue
                        for c in cols:
                            v = sample.get(c.name)
                            if v is None:
                                continue
                            v_j = _to_jsonable(v)
                            if not isinstance(c.sample_values, list):
                                c.sample_values = []
                            if v_j not in c.sample_values and len(c.sample_values) < 5:
                                c.sample_values.append(v_j)
            except Exception:  # noqa: BLE001
                pass

        for c in cols:
            if c.description and c.description_source == "manual":
                # 人工覆写过, 跳过
                n_skipped += 1
                continue

            # 1) 规则推断
            ruled = _rule_infer(c)
            used_llm = False
            if ruled:
                desc, conf, rationale = ruled
                n_rule += 1
            else:
                # 2) LLM 兜底
                try:
                    desc, conf, rationale = await _llm_infer_async(ctx, c, t, profile)
                except Exception:  # noqa: BLE001
                    desc, conf, rationale = _llm_infer_sync(c, t)
                used_llm = True
                n_llm += 1

            if conf < min_confidence or not desc:
                n_skipped += 1
                continue

            # 写 ai_suggestion (双写, 等人工审核)
            sug = AISuggestion(
                suggestion_type="description",
                target_type="column",
                target_id=c.id,
                payload={"description": desc},
                rationale=rationale,
                confidence=conf,
                model="rules" if not used_llm else "llm",
                skill="infer_column_descriptions",
                use_case_id=ctx.use_case_id,
                status="pending",
            )
            ctx.db.add(sug)
            await ctx.db.flush()

            items.append(
                {
                    "column_id": str(c.id),
                    "table_id": str(t.id),
                    "table_fqn": t.fqn,
                    "column_name": c.name,
                    "description": desc,
                    "confidence": conf,
                    "rationale": rationale,
                    "source": "rules" if not used_llm else "llm",
                    "suggestion_id": str(sug.id),
                }
            )

            # apply=true 时直接写
            if apply and conf >= 0.7:
                c.description = desc
                c.description_source = "ai_inferred"
                c.description_rationale = rationale
                sug.status = "auto_applied"
                n_applied += 1
                ctx.log("auto_applied", column_id=str(c.id), desc=desc[:60])

    await ctx.db.commit()

    summary = {
        "tables_processed": len(tables),
        "columns_inferred": len(items),
        "rule_matched": n_rule,
        "llm_used": n_llm,
        "skipped_low_confidence": n_skipped,
        "auto_applied": n_applied,
    }
    return SkillResult(
        ok=True,
        output=SkillOutput(items=items, summary=summary),
    )


async def _llm_infer_async(
    ctx: SkillContext,
    col: ColumnAsset,
    table: TableAsset,
    profile: str,
) -> tuple[str, float, str]:
    """LLM 推断 (用 ctx.llm, profile=cheap)."""
    if ctx.llm is None:
        return _llm_infer_sync(col, table)
    prompt = (
        f"你是一个数据治理专家。基于以下列信息, 用 20-50 字中文描述该列的业务含义。\n"
        f"表: {table.fqn} ({table.description or '无描述'})\n"
        f"列: {col.name} ({col.data_type})\n"
        f"PII 分类: {col.pii_class}\n"
        f"样本值: {col.sample_values[:3] if col.sample_values else '(无)'}\n\n"
        f"输出严格 JSON: {{\"description\": \"...\", \"confidence\": 0.0-1.0}}"
    )
    try:
        resp = await ctx.llm.complete(
            [
                {"role": "system", "content": "你输出严格 JSON。"},
                {"role": "user", "content": prompt},
            ],
            profile=profile,
            temperature=0.2,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        content = resp["content"].strip()
        if content.startswith("```"):
            content = "\n".join(
                l for l in content.splitlines() if not l.strip().startswith("```")
            )
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            content = content[start : end + 1]
        parsed = json.loads(content)
        return (
            parsed.get("description", "").strip() or _llm_infer_sync(col, table)[0],
            float(parsed.get("confidence", 0.6)),
            f"llm_model={resp.get('model','?')}",
        )
    except Exception:  # noqa: BLE001
        return _llm_infer_sync(col, table)
