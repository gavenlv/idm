"""infer_table_description: 用 LLM 推断表/列的业务描述, 写入 ai_suggestion (待人工审核).

Inputs:
    table_ids: list[str]  要推断的 table_asset.id (空 = 全表)
    sample_rows: int      每表采样多少行给 LLM (默认 3)
    min_confidence: float 低于此值的不入建议 (默认 0.5)
    profile: str          LLM profile (default / cheap / planner)

Outputs (SkillOutput.items):
    [{table_id, fqn, suggestion_id, confidence, model}, ...]

写入:
    ai_suggestion (suggestion_type=description, target_type=table, payload={description})
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill
from idm_kg.models.ai_suggestion import AISuggestion
from idm_kg.models.column_asset import ColumnAsset
from idm_kg.models.table_asset import TableAsset

logger = logging.getLogger(__name__)


PROMPT_TEMPLATE = """你是一个资深数据治理专家。基于以下 ClickHouse 表的结构与样本, 用一段简洁的中文 (60-120 字) 描述该表的业务含义。

输出要求:
1. 只输出 JSON: {{\"description\": \"...\", \"confidence\": 0.0-1.0, \"tier\": \"critical|important|normal\"}}
2. description 包含: 业务主题 (订单/用户/...) + 关键字段 (金额/状态/...) + 典型用途
3. confidence 反映你对推断的确信度 (样本越多越高)
4. tier 根据 PII 风险与业务重要性评估

【表】{fqn}
【列】{columns}
【样本 ({n_rows} 行)】{samples}
"""


def _format_columns(cols: list[ColumnAsset]) -> str:
    parts = []
    for c in cols[:50]:  # 限长
        flags = []
        if c.is_primary_key:
            flags.append("PK")
        if not c.nullable:
            flags.append("NN")
        flag_str = (" [" + ",".join(flags) + "]") if flags else ""
        parts.append(f"  - {c.name}: {c.data_type}{flag_str}")
    return "\n".join(parts)


def _format_samples(samples: list[dict[str, Any]], columns: list[str]) -> str:
    if not samples:
        return "(无样本)"
    out = []
    for s in samples[:3]:
        kv = ", ".join(f"{k}={s.get(k)!r}" for k in columns[:8])
        out.append(f"  {{ {kv} }}")
    return "\n".join(out)


def _parse_llm_json(content: str) -> dict[str, Any]:
    """LLM 输出未必是纯 JSON, 尝试多种解析."""
    content = content.strip()
    # 去掉 ```json ... ``` 包裹
    if content.startswith("```"):
        lines = content.splitlines()
        content = "\n".join(l for l in lines if not l.strip().startswith("```"))
    # 找到第一个 { 和最后一个 }
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        content = content[start : end + 1]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {
            "description": content[:200],
            "confidence": 0.4,
            "tier": "normal",
        }


@skill(name="infer_table_description", version=1, agent="doc")
async def infer_table_description(ctx: SkillContext, **inputs: Any) -> SkillResult:
    table_ids: list[str] = inputs.get("table_ids") or []
    sample_rows: int = int(inputs.get("sample_rows") or 3)
    min_confidence: float = float(inputs.get("min_confidence") or 0.5)
    profile: str = inputs.get("profile") or "default"

    if ctx.db is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.db is None")

    # 1) 选表
    if table_ids:
        stmt = select(TableAsset).where(TableAsset.id.in_(table_ids))
    else:
        stmt = select(TableAsset).where(TableAsset.description.is_(None)).limit(20)
    tables = list((await ctx.db.execute(stmt)).scalars().all())
    ctx.log("tables_selected", count=len(tables))

    if not tables:
        return SkillResult(
            ok=True,
            output=SkillOutput(items=[], summary={"reason": "no tables to infer"}),
        )

    items: list[dict[str, Any]] = []
    skipped = 0

    mcp = ctx.mcp.get("clickhouse")
    for t in tables:
        # 取列
        cols = list(
            (await ctx.db.execute(select(ColumnAsset).where(ColumnAsset.table_id == t.id))).scalars()
        )
        col_names = [c.name for c in cols]
        # 采样
        samples: list[dict[str, Any]] = []
        if mcp is not None and sample_rows > 0:
            try:
                # fqn: svc.db.schema.tbl
                parts = t.fqn.split(".")
                if len(parts) == 4:
                    _, db_name, _, tbl_name = parts
                    samples = mcp.sample_rows(db_name, tbl_name, limit=sample_rows)
            except Exception:  # noqa: BLE001
                samples = []

        # 调 LLM
        prompt = PROMPT_TEMPLATE.format(
            fqn=t.fqn,
            columns=_format_columns(cols),
            n_rows=len(samples),
            samples=_format_samples(samples, col_names),
        )
        llm_resp = await ctx.llm.complete(
            [
                {"role": "system", "content": "你输出严格 JSON, 不含多余文字。"},
                {"role": "user", "content": prompt},
            ],
            profile=profile,
            temperature=0.2,
            max_tokens=400,
            response_format={"type": "json_object"},
        )
        parsed = _parse_llm_json(llm_resp["content"])
        confidence = float(parsed.get("confidence") or 0.5)
        description = (parsed.get("description") or "").strip()
        tier_suggested = parsed.get("tier") or t.tier
        if tier_suggested not in ("critical", "important", "normal"):
            tier_suggested = "normal"

        if confidence < min_confidence or not description:
            skipped += 1
            continue

        # 写 ai_suggestion
        sug = AISuggestion(
            suggestion_type="description",
            target_type="table",
            target_id=t.id,
            payload={"description": description, "tier": tier_suggested},
            rationale=f"LLM={llm_resp['model']} tier={llm_resp['tier']} confidence={confidence}",
            confidence=confidence,
            model=llm_resp["model"],
            skill="infer_table_description",
            use_case_id=ctx.use_case_id,
            prompt_hash=llm_resp.get("prompt_hash"),
            langfuse_trace_id=None,
            status="pending",
        )
        ctx.db.add(sug)
        await ctx.db.flush()
        items.append(
            {
                "table_id": str(t.id),
                "fqn": t.fqn,
                "suggestion_id": str(sug.id),
                "confidence": confidence,
                "model": llm_resp["model"],
                "tier_suggested": tier_suggested,
            }
        )
        ctx.log("inferred", fqn=t.fqn, confidence=confidence, model=llm_resp["model"])

    await ctx.db.commit()

    summary = {
        "tables_total": len(tables),
        "suggestions_created": len(items),
        "skipped_low_confidence": skipped,
        "model": items[0]["model"] if items else "n/a",
    }
    return SkillResult(
        ok=True,
        output=SkillOutput(
            items=items,
            summary=summary,
            artifacts=[i["suggestion_id"] for i in items],
        ),
    )
