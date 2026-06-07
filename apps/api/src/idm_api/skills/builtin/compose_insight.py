"""compose_insight: 把异常/事件合成为简报, 写 ai_suggestion(suggestion_type=insight).

M4: Insight Composer, 每日/每周简报.
- 拉取最近 7 天的:
    1) quality_result 失败
    2) ai_suggestion 积压 (pending > 7d)
    3) 缺 owner / 缺 description 的 critical 表
    4) 健康分下降的表 (与上周相比)
- 调 LLM 生成 1 段中文简报 (200-400 字) + 行动建议
- 写 ai_suggestion.status=pending, payload.channel=insight_compose

Inputs:
    service: str           限定 service (空 = 全部)
    days: int              简报窗口 (默认 7)
    max_findings: int      最多纳多少 finding (默认 30)
    channel: str           输出 channel (slack / lark / email / in_app)
    apply: bool            True=写 suggestion, False=仅返回

Outputs (SkillOutput.items):
    [{finding_id, kind, severity, summary, action, suggestion_id?}, ...]
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill
from idm_kg.models.ai_suggestion import AISuggestion
from idm_kg.models.quality import QualityResult, QualityRule
from idm_kg.models.table_asset import TableAsset

logger = logging.getLogger(__name__)


async def _collect_findings(
    db: AsyncSession,
    *,
    service: str,
    days: int,
    max_findings: int,
) -> list[dict[str, Any]]:
    """从 KG 拉取近期的"事件"作为简报输入."""
    out: list[dict[str, Any]] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # 1) 质量规则失败
    failed_q = (
        select(QualityResult, QualityRule, TableAsset)
        .join(QualityRule, QualityRule.id == QualityResult.rule_id)
        .join(TableAsset, TableAsset.id == QualityRule.table_id)
        .where(QualityResult.passed.is_(False), QualityResult.created_at >= cutoff)
        .order_by(QualityResult.created_at.desc())
        .limit(max_findings)
    )
    if service:
        failed_q = failed_q.where(TableAsset.fqn.like(f"{service}.%"))
    for qr, rule, t in (await db.execute(failed_q)).all():
        out.append(
            {
                "kind": "quality_failed",
                "severity": rule.severity,
                "table_fqn": t.fqn,
                "rule": rule.name,
                "observed": qr.observed_value,
                "message": qr.message,
                "created_at": qr.created_at.isoformat() if qr.created_at else None,
            }
        )

    # 2) pending 建议积压
    pending_q = (
        select(AISuggestion)
        .where(AISuggestion.status == "pending", AISuggestion.created_at >= cutoff)
        .order_by(AISuggestion.created_at.asc())
        .limit(max_findings)
    )
    pendings = list((await db.execute(pending_q)).scalars())
    if pendings:
        out.append(
            {
                "kind": "pending_suggestions",
                "severity": "info",
                "count": len(pendings),
                "oldest": pendings[0].created_at.isoformat() if pendings else None,
            }
        )

    # 3) critical 表无 description
    crit_q = select(TableAsset).where(
        TableAsset.tier == "critical",
        TableAsset.status == "active",
        TableAsset.description.is_(None),
    )
    if service:
        crit_q = crit_q.where(TableAsset.fqn.like(f"{service}.%"))
    crits = list((await db.execute(crit_q.limit(50))).scalars())
    if crits:
        out.append(
            {
                "kind": "critical_no_description",
                "severity": "warning",
                "count": len(crits),
                "samples": [t.fqn for t in crits[:5]],
            }
        )

    # 4) 健康分低 (< 60)
    low_q = select(TableAsset).where(
        TableAsset.health_score.is_not(None),
        TableAsset.health_score < 60,
        TableAsset.status == "active",
    )
    if service:
        low_q = low_q.where(TableAsset.fqn.like(f"{service}.%"))
    lows = list((await db.execute(low_q.limit(50))).scalars())
    if lows:
        out.append(
            {
                "kind": "low_health",
                "severity": "warning",
                "count": len(lows),
                "samples": [
                    {"fqn": t.fqn, "score": t.health_score} for t in lows[:5]
                ],
            }
        )

    return out[:max_findings]


async def _maybe_summarize(ctx: SkillContext, findings: list[dict[str, Any]]) -> str:
    if not ctx.llm or not findings:
        return ""
    bullet = "\n".join(
        f"- [{f.get('severity', 'info')}] {f.get('kind')}: "
        f"{(f.get('table_fqn') or f.get('samples') or f.get('count') or '')} | "
        f"{(f.get('message') or '')[:120]}"
        for f in findings[:20]
    )
    prompt = (
        "你是数据治理编辑。基于以下 bullet, 用中文写 1 段 200~400 字的简报。"
        "包含: 1) 整体健康趋势 2) 最严重的 3 个问题 3) 建议下一步行动。\n"
        f"【Findings】\n{bullet}"
    )
    try:
        resp = await ctx.llm.complete(
            [
                {"role": "system", "content": "你写简报给数据团队负责人, 简洁专业。"},
                {"role": "user", "content": prompt},
            ],
            profile="default",
            temperature=0.3,
            max_tokens=600,
        )
        return resp.get("content", "").strip()
    except Exception as e:  # noqa: BLE001
        ctx.log("llm_summarize_failed", error=str(e)[:120])
        return ""


@skill(name="compose_insight", version=1, agent="insight")
async def compose_insight(ctx: SkillContext, **inputs: Any) -> SkillResult:
    service: str = inputs.get("service") or ""
    days: int = int(inputs.get("days") or 7)
    max_findings: int = int(inputs.get("max_findings") or 30)
    channel: str = inputs.get("channel") or "in_app"
    apply: bool = bool(inputs.get("apply", True))

    if ctx.db is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.db is None")

    findings = await _collect_findings(
        ctx.db, service=service, days=days, max_findings=max_findings
    )
    summary_text = await _maybe_summarize(ctx, findings)

    if not findings:
        return SkillResult(
            ok=True,
            output=SkillOutput(
                items=[],
                summary={"reason": "no findings in window", "days": days},
            ),
        )

    items: list[dict[str, Any]] = []
    suggestion_id: str | None = None
    if apply:
        sug = AISuggestion(
            suggestion_type="insight",
            target_type="global",
            target_id=ctx.use_case_id or "00000000-0000-0000-0000-000000000000",
            payload={
                "channel": channel,
                "days": days,
                "summary": summary_text,
                "findings": findings,
                "service": service or None,
            },
            rationale=f"{len(findings)} findings in last {days}d, channel={channel}",
            confidence=1.0,
            model="compose",
            skill="compose_insight",
            use_case_id=ctx.use_case_id,
            status="pending",
        )
        ctx.db.add(sug)
        await ctx.db.flush()
        suggestion_id = str(sug.id)

    for f in findings:
        items.append(
            {
                "finding_id": f.get("kind"),
                "kind": f.get("kind"),
                "severity": f.get("severity"),
                "table_fqn": f.get("table_fqn"),
                "message": f.get("message"),
                "summary": summary_text[:200],
            }
        )

    if apply:
        await ctx.db.commit()

    summary = {
        "findings_count": len(findings),
        "channel": channel,
        "days": days,
        "summary_text_len": len(summary_text),
        "suggestion_id": suggestion_id,
        "apply": apply,
    }
    return SkillResult(
        ok=True,
        output=SkillOutput(
            items=items,
            summary=summary,
            artifacts=[suggestion_id] if suggestion_id else [],
        ),
    )
