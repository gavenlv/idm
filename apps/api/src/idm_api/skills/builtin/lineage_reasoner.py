"""lineage_reasoner: 用 LLM 推断隐式血缘 (跨系统 / 跨命名约定的表间关系).

典型场景:
- 命名约定 A -> 命名约定 B (stg_orders -> fct_orders_daily)
- 跨数仓写入: 业务表名 vs dbt model 名
- 跨服务: ClickHouse ODS -> ClickHouse ADS

Inputs:
    service: str            限定 service 下的表 (空 = 全部)
    min_confidence: float   低于此值不入 (默认 0.6)
    use_llm: bool           是否调 LLM (默认 True)
    apply: bool             True=直接写 lineage (source=ai_inferred); False=写 suggestion
    limit: int              最多处理多少表 (默认 50)

Outputs (SkillOutput.items):
    [{upstream_fqn, downstream_fqn, confidence, reasoning,
      lineage_id|suggestion_id}, ...]

写入:
    table_lineage (apply=True)  或  ai_suggestion suggestion_type=lineage
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
from idm_kg.models.table_asset import TableAsset
from idm_kg.models.table_lineage import TableLineage

logger = logging.getLogger(__name__)


PROMPT = """你是数据血缘推断专家。基于表名/描述/列, 判断上游 (upstream) → 下游 (downstream) 的关系。
不要凭感觉, 只输出有强线索的推断 (命名一致 / 描述提到 / 列重叠)。

输出严格 JSON: {"edges": [{"upstream_fqn":"...","downstream_fqn":"...","confidence":0.0-1.0,"reasoning":"<30字中文>"}]}

【候选上游】
{ups}
【候选下游】
{downs}
"""


def _name_similarity(a: str, b: str) -> float:
    """简化 token Jaccard: 表名去掉前缀后的 token 重叠率."""
    def tok(s: str) -> set[str]:
        s = s.split(".")[-1]
        s = re.sub(r"^(stg|ods|dwd|dws|ads|fct|dim)_", "", s.lower())
        return {t for t in re.findall(r"[a-z0-9]+", s) if len(t) >= 3}
    ta, tb = tok(a), tok(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    return len(inter) / max(1, len(ta | tb))


def _heuristic_pairs(
    candidates: list[TableAsset], min_sim: float = 0.4
) -> list[dict[str, Any]]:
    """基于命名 + 描述 token 重叠的启发式配对."""
    pairs: list[dict[str, Any]] = []
    for i, up in enumerate(candidates):
        for dn in candidates[i + 1 :]:
            # 排除同一张表
            if up.id == dn.id:
                continue
            sim = _name_similarity(up.fqn, dn.fqn)
            if sim < min_sim:
                continue
            # 简单的方向启发: 有 stg_ 前缀的算上游; 有 fct_ / dim_ 的算下游
            up_layer = up.fqn.split(".")[-1].lower().split("_")[0]
            dn_layer = dn.fqn.split(".")[-1].lower().split("_")[0]
            if up_layer in ("stg", "ods", "src", "raw") and dn_layer in ("fct", "dim", "dwd", "dws", "ads", "mart"):
                upstream, downstream = up, dn
            elif up_layer in ("fct", "dim", "dwd", "dws", "ads", "mart") and dn_layer in ("stg", "ods", "src", "raw"):
                upstream, downstream = dn, up
            else:
                # 默认按服务名顺序
                upstream, downstream = up, dn
            pairs.append(
                {
                    "upstream": upstream,
                    "downstream": downstream,
                    "confidence": round(min(0.9, 0.4 + sim), 3),
                    "reasoning": f"name sim={sim:.2f} ({up_layer}->{dn_layer})",
                }
            )
    pairs.sort(key=lambda x: -x["confidence"])
    return pairs


async def _maybe_llm_pairs(
    ctx: SkillContext,
    ups: list[TableAsset],
    downs: list[TableAsset],
) -> list[dict[str, Any]]:
    if not ctx.llm or not ups or not downs:
        return []
    up_lines = "\n".join(
        f"  - {t.fqn} | tier={t.tier} | {t.description or '(no desc)'}" for t in ups[:30]
    )
    dn_lines = "\n".join(
        f"  - {t.fqn} | tier={t.tier} | {t.description or '(no desc)'}" for t in downs[:30]
    )
    try:
        resp = await ctx.llm.complete(
            [
                {"role": "system", "content": "只输出 JSON, 不含解释。"},
                {"role": "user", "content": PROMPT.format(ups=up_lines, downs=dn_lines)},
            ],
            profile="default",
            temperature=0.1,
            max_tokens=600,
            response_format={"type": "json_object"},
        )
        text = resp["content"].strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return []
        data = json.loads(text[start : end + 1])
        edges = data.get("edges") or []
        out: list[dict[str, Any]] = []
        for e in edges:
            uf = (e.get("upstream_fqn") or "").lower()
            df = (e.get("downstream_fqn") or "").lower()
            try:
                conf = float(e.get("confidence") or 0.5)
            except (TypeError, ValueError):
                conf = 0.5
            if uf and df and uf != df:
                out.append(
                    {
                        "upstream_fqn": uf,
                        "downstream_fqn": df,
                        "confidence": max(0.0, min(1.0, conf)),
                        "reasoning": (e.get("reasoning") or "").strip()[:200],
                        "model": resp.get("model", "n/a"),
                    }
                )
        return out
    except Exception as e:  # noqa: BLE001
        ctx.log("llm_lineage_failed", error=str(e)[:120])
        return []


@skill(name="lineage_reasoner", version=1, agent="lineage")
async def lineage_reasoner(ctx: SkillContext, **inputs: Any) -> SkillResult:
    service: str = inputs.get("service") or ""
    min_confidence: float = float(inputs.get("min_confidence") or 0.6)
    use_llm: bool = bool(inputs.get("use_llm", True))
    apply: bool = bool(inputs.get("apply", False))
    limit: int = int(inputs.get("limit") or 50)

    if ctx.db is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.db is None")

    # 1) 选表
    stmt = select(TableAsset).where(TableAsset.status == "active")
    if service:
        stmt = stmt.where(TableAsset.fqn.like(f"{service}.%"))
    tables = list((await ctx.db.execute(stmt.limit(limit))).scalars().all())
    if not tables:
        return SkillResult(
            ok=True,
            output=SkillOutput(items=[], summary={"reason": "no tables"}),
        )

    # 2) 启发式配对
    pairs = _heuristic_pairs(tables)
    pairs = [p for p in pairs if p["confidence"] >= min_confidence]

    # 3) 兜底: LLM 推断 (取置信度较低的)
    if use_llm and len(pairs) < 5 and ctx.llm:
        ups = list({p["upstream"] for p in pairs})
        # 用所有表作为 downstream 候选
        llm_edges = await _maybe_llm_pairs(ctx, tables[:30], tables[:30])
        for e in llm_edges:
            if e["confidence"] < min_confidence:
                continue
            pairs.append(
                {
                    "upstream_fqn": e["upstream_fqn"],
                    "downstream_fqn": e["downstream_fqn"],
                    "confidence": e["confidence"],
                    "reasoning": e.get("reasoning", ""),
                }
            )

    if not pairs:
        return SkillResult(
            ok=True,
            output=SkillOutput(items=[], summary={"reason": "no candidates", "tables": len(tables)}),
        )

    # 4) 写库 / 写建议
    items: list[dict[str, Any]] = []
    added = 0
    skipped = 0
    fqn_to_id: dict[str, str] = {t.fqn: str(t.id) for t in tables}

    for p in pairs[:200]:
        if isinstance(p["upstream"], TableAsset):
            up_fqn = p["upstream"].fqn
            dn_fqn = p["downstream"].fqn
        else:
            up_fqn = p["upstream_fqn"]
            dn_fqn = p["downstream_fqn"]
        up_id = fqn_to_id.get(up_fqn)
        dn_id = fqn_to_id.get(dn_fqn)
        if not up_id or not dn_id:
            skipped += 1
            continue
        if apply:
            stmt_ins = (
                pg_insert(TableLineage)
                .values(
                    upstream_id=up_id,
                    downstream_id=dn_id,
                    transform_type="ai_inferred",
                    confidence=p["confidence"],
                    source="ai_inferred",
                    extra={"reasoning": p.get("reasoning", "")[:200]},
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        TableLineage.upstream_id,
                        TableLineage.downstream_id,
                        TableLineage.transform_type,
                    ]
                )
            )
            await ctx.db.execute(stmt_ins)
            added += 1
            lineage_id = None
            sug_id = None
        else:
            sug = AISuggestion(
                suggestion_type="lineage",
                target_type="table",
                target_id=up_id,  # 用 upstream 作为 target, payload 标 downstream
                payload={
                    "upstream_fqn": up_fqn,
                    "downstream_fqn": dn_fqn,
                    "confidence": p["confidence"],
                    "reasoning": p.get("reasoning", ""),
                },
                rationale=p.get("reasoning") or f"ai inferred conf={p['confidence']}",
                confidence=p["confidence"],
                model="n/a",
                skill="lineage_reasoner",
                use_case_id=ctx.use_case_id,
                status="pending",
            )
            ctx.db.add(sug)
            await ctx.db.flush()
            lineage_id = None
            sug_id = str(sug.id)

        items.append(
            {
                "upstream_fqn": up_fqn,
                "downstream_fqn": dn_fqn,
                "confidence": p["confidence"],
                "reasoning": p.get("reasoning", ""),
                "lineage_id": lineage_id,
                "suggestion_id": sug_id,
            }
        )

    if apply:
        await ctx.db.commit()

    summary = {
        "tables_scanned": len(tables),
        "pairs_inferred": len(items),
        "skipped_unresolved": skipped,
        "lineage_edges_added": added,
        "apply": apply,
    }
    return SkillResult(
        ok=True,
        output=SkillOutput(
            items=items,
            summary=summary,
            artifacts=[(i.get("lineage_id") or i.get("suggestion_id") or "") for i in items],
        ),
    )
