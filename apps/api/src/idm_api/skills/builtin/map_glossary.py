"""map_glossary: 用 LLM 把表的列名 / 业务含义映射到 glossary_term, 写 asset_term (suggestion 审核流)。

Inputs:
    table_ids: list[str]       仅扫描这些表 (空 = 全部)
    service: str               仅扫某 service 下的表 (空 = 全部)
    min_confidence: float      低于此值不入 (默认 0.55)
    apply: bool                True=直接写 asset_term; False=写 ai_suggestion (人工审)

Outputs (SkillOutput.items):
    [{table_id, table_fqn, term, definition, confidence, source,
      asset_term_id|suggestion_id}, ...]

写入:
    asset_term (apply=True)  或  ai_suggestion suggestion_type=glossary
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill
from idm_kg.models.ai_suggestion import AISuggestion
from idm_kg.models.column_asset import ColumnAsset
from idm_kg.models.glossary import AssetTerm, GlossaryTerm
from idm_kg.models.table_asset import TableAsset

logger = logging.getLogger(__name__)


PROMPT_TEMPLATE = """你是数据治理专家。基于表的列名与业务描述, 推断该表最可能绑定的业务术语。
只能从【候选术语】里选, 不要自创。

输出严格 JSON:
{{"matches": [
  {{"term": "<候选术语名>", "confidence": 0.0-1.0, "reasoning": "<20-40 字中文>"}}
]}}

规则:
- 至少 1 个, 至多 3 个匹配
- confidence 反映列名 / 描述与术语定义 / 同义词 的契合度
- 选不出时返回 {{"matches": []}}

【表 FQN】{fqn}
【描述】{description}
【列名】{columns}
【候选术语 (name | definition | synonyms)】
{candidates}
"""


def _format_columns(cols: list[ColumnAsset], limit: int = 20) -> str:
    return ", ".join(c.name for c in cols[:limit])


def _parse_llm_json(content: str) -> list[dict[str, Any]]:
    content = (content or "").strip()
    if content.startswith("```"):
        content = "\n".join(
            l for l in content.splitlines() if not l.strip().startswith("```")
        )
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return []
    matches = data.get("matches") or []
    out: list[dict[str, Any]] = []
    for m in matches:
        if not isinstance(m, dict):
            continue
        term = (m.get("term") or "").strip()
        if not term:
            continue
        try:
            conf = float(m.get("confidence") or 0.5)
        except (TypeError, ValueError):
            conf = 0.5
        out.append(
            {
                "term": term,
                "confidence": max(0.0, min(1.0, conf)),
                "reasoning": (m.get("reasoning") or "").strip()[:200],
            }
        )
    return out


def _heuristic_matches(t: TableAsset, candidates: list[GlossaryTerm]) -> list[dict[str, Any]]:
    """纯规则: 列名 / 描述 / FQN 与术语名 / 同义词 命中 → 给个 0.6~0.7 的基础分."""
    cols = t.fqn.lower() + " " + (t.description or "").lower()
    out: list[dict[str, Any]] = []
    for term in candidates:
        score = 0.0
        # term.name 命中
        if term.name.lower() in cols:
            score = max(score, 0.7)
        # synonyms 命中
        for syn in term.synonyms or []:
            if syn and syn.lower() in cols:
                score = max(score, 0.65)
                break
        # 缩写 / token 重叠
        for tok in re.findall(r"[a-z0-9_]+", cols):
            if len(tok) >= 3 and (tok == term.name.lower() or tok in (s.lower() for s in term.synonyms or [])):
                score = max(score, 0.6)
        if score > 0:
            out.append(
                {
                    "term": term.name,
                    "confidence": score,
                    "reasoning": "fqn/desc 列名命中",
                }
            )
    out.sort(key=lambda x: x["confidence"], reverse=True)
    return out[:3]


async def _maybe_llm(
    ctx: SkillContext, t: TableAsset, cols: list[ColumnAsset], candidates: list[GlossaryTerm]
) -> list[dict[str, Any]]:
    if not ctx.llm or not candidates:
        return []
    cand_lines = "\n".join(
        f"  - {c.name} | {c.definition[:120]} | {','.join(c.synonyms or [])}"
        for c in candidates[:30]
    )
    prompt = PROMPT_TEMPLATE.format(
        fqn=t.fqn,
        description=(t.description or "(无)"),
        columns=_format_columns(cols),
        candidates=cand_lines,
    )
    try:
        resp = await ctx.llm.complete(
            [
                {"role": "system", "content": "只输出 JSON, 不含解释。"},
                {"role": "user", "content": prompt},
            ],
            profile="default",
            temperature=0.1,
            max_tokens=400,
            response_format={"type": "json_object"},
        )
        return _parse_llm_json(resp["content"])
    except Exception as e:  # noqa: BLE001
        ctx.log("llm_glossary_failed", fqn=t.fqn, error=str(e)[:120])
        return []


@skill(name="map_glossary", version=1, agent="glossary")
async def map_glossary(ctx: SkillContext, **inputs: Any) -> SkillResult:
    table_ids: list[str] = inputs.get("table_ids") or []
    service: str = inputs.get("service") or ""
    min_confidence: float = float(inputs.get("min_confidence") or 0.55)
    apply: bool = bool(inputs.get("apply", False))
    use_llm: bool = bool(inputs.get("use_llm", True))

    if ctx.db is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.db is None")

    # 1) 候选术语
    terms = list(
        (
            await ctx.db.execute(
                select(GlossaryTerm).order_by(GlossaryTerm.name).limit(500)
            )
        ).scalars()
    )
    if not terms:
        return SkillResult(
            ok=True,
            output=SkillOutput(items=[], summary={"reason": "no glossary terms"}),
        )
    term_by_name: dict[str, GlossaryTerm] = {t.name: t for t in terms}

    # 2) 选表
    stmt = select(TableAsset)
    if table_ids:
        stmt = stmt.where(TableAsset.id.in_(table_ids))
    if service:
        stmt = stmt.where(TableAsset.fqn.like(f"{service}.%"))
    tables = list((await ctx.db.execute(stmt.limit(200))).scalars().all())
    ctx.log("tables_selected", count=len(tables), candidates=len(terms))

    if not tables:
        return SkillResult(
            ok=True,
            output=SkillOutput(items=[], summary={"reason": "no tables to map"}),
        )

    items: list[dict[str, Any]] = []
    skipped = 0
    llm_calls = 0

    for t in tables:
        cols = list(
            (await ctx.db.execute(select(ColumnAsset).where(ColumnAsset.table_id == t.id))).scalars()
        )

        # 3) 启发式 → 不足 0.65 时再 LLM
        matches = _heuristic_matches(t, terms)
        if (not matches or matches[0]["confidence"] < 0.65) and use_llm:
            llm_calls += 1
            llm_matches = await _maybe_llm(ctx, t, cols, terms)
            if llm_matches:
                # 合并: 用 LLM 替换启发式 (LLM 优先)
                matches = llm_matches

        if not matches:
            skipped += 1
            continue

        for m in matches:
            if m["confidence"] < min_confidence:
                skipped += 1
                continue
            term_name = m["term"]
            term = term_by_name.get(term_name)
            if not term:
                ctx.log("term_not_found", term=term_name)
                skipped += 1
                continue

            if apply:
                stmt_ins = (
                    pg_insert(AssetTerm)
                    .values(
                        table_id=t.id,
                        term_id=term.id,
                        confidence=m["confidence"],
                        source="ai_inferred" if use_llm else "fqn_inference",
                    )
                    .on_conflict_do_nothing(
                        index_elements=[AssetTerm.table_id, AssetTerm.term_id]
                    )
                )
                await ctx.db.execute(stmt_ins)
                row = (
                    await ctx.db.execute(
                        select(AssetTerm).where(
                            AssetTerm.table_id == t.id, AssetTerm.term_id == term.id
                        )
                    )
                ).scalar_one_or_none()
                asset_term_id = str(row.id) if row else None
                sug_id = None
            else:
                sug = AISuggestion(
                    suggestion_type="glossary",
                    target_type="table",
                    target_id=t.id,
                    payload={
                        "term_id": str(term.id),
                        "term": term.name,
                        "table_fqn": t.fqn,
                        "confidence": m["confidence"],
                    },
                    rationale=m.get("reasoning") or f"glossary mapping conf={m['confidence']}",
                    confidence=m["confidence"],
                    model=(ctx.llm.last_model if ctx.llm and hasattr(ctx.llm, "last_model") else "n/a"),
                    skill="map_glossary",
                    use_case_id=ctx.use_case_id,
                    status="pending",
                )
                ctx.db.add(sug)
                await ctx.db.flush()
                asset_term_id = None
                sug_id = str(sug.id)

            items.append(
                {
                    "table_id": str(t.id),
                    "table_fqn": t.fqn,
                    "term": term.name,
                    "definition": term.definition,
                    "confidence": m["confidence"],
                    "reasoning": m.get("reasoning", ""),
                    "asset_term_id": asset_term_id,
                    "suggestion_id": sug_id,
                }
            )

    await ctx.db.commit()

    summary = {
        "tables_scanned": len(tables),
        "mappings_inferred": len(items),
        "skipped_low_confidence": skipped,
        "candidates": len(terms),
        "llm_calls": llm_calls,
        "apply": apply,
    }
    return SkillResult(
        ok=True,
        output=SkillOutput(
            items=items,
            summary=summary,
            artifacts=[(i.get("asset_term_id") or i.get("suggestion_id") or "") for i in items],
        ),
    )
