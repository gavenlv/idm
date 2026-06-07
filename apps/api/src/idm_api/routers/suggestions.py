"""/api/v1/suggestions: LLM 建议审核 (AI in the Loop, Human in the Lead).

AGENT_INSTRUCTIONS §1 原则 5:
- 所有 LLM 写入建议先入 ai_suggestions (status=pending)
- 人工 / 策略 approve/reject
- approve 后才写正式字段 (description / pii_class / owner / ...)

M1: approve 后按 suggestion_type 分发, 同步到目标表:
    - description → table_asset.description
    - pii_class   → column_asset.pii_class / pii_confidence / pii_source
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.db import get_db
from idm_api.schemas import (
    SuggestionApprove,
    SuggestionListResponse,
    SuggestionRead,
)
from idm_kg.models.ai_suggestion import AISuggestion
from idm_kg.models.audit_log import AuditLog
from idm_kg.models.column_asset import ColumnAsset
from idm_kg.models.table_asset import TableAsset

router = APIRouter()


@router.get("", response_model=SuggestionListResponse, summary="List suggestions")
async def list_suggestions(
    status_filter: str = Query("pending", alias="status", pattern="^(pending|approved|rejected|auto_approved|expired)$"),
    suggestion_type: str | None = None,
    target_type: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> SuggestionListResponse:
    """列出 LLM 建议 (默认 pending)。"""
    base = select(AISuggestion).where(AISuggestion.status == status_filter)
    if suggestion_type:
        base = base.where(AISuggestion.suggestion_type == suggestion_type)
    if target_type:
        base = base.where(AISuggestion.target_type == target_type)

    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = base.order_by(AISuggestion.confidence.desc(), AISuggestion.created_at.desc()).limit(limit).offset(offset)
    items = list((await db.execute(stmt)).scalars().all())

    return SuggestionListResponse(
        items=[SuggestionRead.model_validate(i) for i in items],
        total=total,
    )


@router.post("/{suggestion_id}/approve", response_model=SuggestionRead, summary="Approve suggestion")
async def approve_suggestion(
    suggestion_id: UUID,
    body: SuggestionApprove = SuggestionApprove(),
    actor: str = "system:tester",  # M3 改为从 JWT 拿
    db: AsyncSession = Depends(get_db),
) -> AISuggestion:
    """批准 LLM 建议。

    M1: 仅标 status=approved + 写审计。
    M2+: 同步到目标表 (description / pii_class / owner ...)。
    """
    sug = await db.get(AISuggestion, suggestion_id)
    if sug is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found")
    if sug.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Suggestion already {sug.status}",
        )

    sug.status = "approved"
    sug.reviewed_by = actor
    sug.reviewed_at = datetime.now(timezone.utc)
    sug.review_note = body.review_note

    # 按 suggestion_type 分发同步到目标表
    sync_msg = await _apply_suggestion(db, sug)

    db.add(
        AuditLog(
            actor=actor,
            action="approve_suggestion",
            resource_type="ai_suggestion",
            resource_id=str(suggestion_id),
            payload={
                "suggestion_type": sug.suggestion_type,
                "target_type": sug.target_type,
                "target_id": str(sug.target_id),
                "confidence": sug.confidence,
                "model": sug.model,
                "sync": sync_msg,
            },
        )
    )
    await db.flush()
    return sug


async def _apply_suggestion(db: AsyncSession, sug: AISuggestion) -> str:
    """Approve 后把 payload 同步到目标表. 返回简短描述供审计."""
    payload = sug.payload or {}
    if sug.suggestion_type == "description" and sug.target_type == "table":
        t = await db.get(TableAsset, sug.target_id)
        if t is None:
            return "table missing"
        new_desc = payload.get("description")
        if new_desc:
            t.description = new_desc[:2048]
        new_tier = payload.get("tier")
        if new_tier in ("critical", "important", "normal"):
            t.tier = new_tier
        return f"table.description <- {len(new_desc or '')} chars"

    if sug.suggestion_type == "pii_class" and sug.target_type == "column":
        c = await db.get(ColumnAsset, sug.target_id)
        if c is None:
            return "column missing"
        pii = payload.get("pii_class")
        if pii:
            c.pii_class = pii
        c.pii_confidence = sug.confidence
        c.pii_source = "ai_inferred"
        return f"column.pii_class <- {pii}"

    return "(no sync handler for this type yet)"


@router.post("/{suggestion_id}/reject", response_model=SuggestionRead, summary="Reject suggestion")
async def reject_suggestion(
    suggestion_id: UUID,
    body: SuggestionApprove = SuggestionApprove(),
    actor: str = "system:tester",
    db: AsyncSession = Depends(get_db),
) -> AISuggestion:
    """拒绝 LLM 建议。"""
    sug = await db.get(AISuggestion, suggestion_id)
    if sug is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found")
    if sug.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Suggestion already {sug.status}",
        )

    sug.status = "rejected"
    sug.reviewed_by = actor
    sug.reviewed_at = datetime.now(timezone.utc)
    sug.review_note = body.review_note

    db.add(
        AuditLog(
            actor=actor,
            action="reject_suggestion",
            resource_type="ai_suggestion",
            resource_id=str(suggestion_id),
            payload={
                "suggestion_type": sug.suggestion_type,
                "target_id": str(sug.target_id),
                "note": body.review_note,
            },
        )
    )
    await db.flush()
    return sug
