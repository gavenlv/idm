"""/api/v1/quality: 数据质量 Dashboard + 规则 CRUD.

M1.5 (Data Quality 提前). 流程:
  GET  /api/v1/quality/dashboard          健康分概览 + 异常列表
  GET  /api/v1/quality/rules             列出规则
  POST /api/v1/quality/rules             新建规则
  POST /api/v1/quality/rules/{id}/run    立即跑一次 (走 run_quality_check skill)
  GET  /api/v1/quality/top-low           健康分 < 70 的表
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.db import get_db
from idm_api.schemas import (
    QualityDashboard,
    QualityRuleCreate,
    QualityRuleListResponse,
    QualityRuleRead,
)
from idm_kg.models.ai_suggestion import AISuggestion
from idm_kg.models.quality import QualityResult, QualityRule
from idm_kg.models.table_asset import TableAsset

router = APIRouter()


@router.get(
    "/dashboard",
    response_model=QualityDashboard,
    summary="Quality dashboard: health score, low tables, recent anomalies",
)
async def dashboard(db: AsyncSession = Depends(get_db)) -> QualityDashboard:
    """汇总健康分 / 异常 / 规则统计, 供 QualityPage 顶部 stats 用."""
    # 1) 健康分
    health_rows = (
        await db.execute(
            select(TableAsset.health_score, TableAsset.health_score_updated_at)
        )
    ).all()
    scores = [r[0] for r in health_rows if r[0] is not None]
    avg = round(sum(scores) / max(1, len(scores)), 1) if scores else None
    tables_low = sum(1 for s in scores if s < 70)
    tables_critical = sum(1 for s in scores if s < 40)
    last_run = max((r[1] for r in health_rows if r[1] is not None), default=None)
    tables_total = (
        await db.execute(select(func.count(TableAsset.id)))
    ).scalar_one()

    # 2) 规则
    rules_total = (
        await db.execute(select(func.count(QualityRule.id)))
    ).scalar_one()

    # 3) 最近异常 (ai_suggestion.suggestion_type='insight' 或 payload.anomaly_kind)
    rows = list(
        (
            await db.execute(
                select(AISuggestion)
                .where(AISuggestion.suggestion_type == "insight")
                .order_by(AISuggestion.created_at.desc())
                .limit(20)
            )
        ).scalars()
    )
    recent = [
        {
            "id": str(r.id),
            "payload": r.payload,
            "status": r.status,
            "confidence": r.confidence,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]

    return QualityDashboard(
        avg_health_score=avg,
        tables_total=tables_total,
        tables_low=tables_low,
        tables_critical=tables_critical,
        rules_total=rules_total,
        rules_failing=0,  # TODO: join quality_results
        recent_anomalies=recent,
        last_run=last_run,
    )


@router.get(
    "/rules",
    response_model=QualityRuleListResponse,
    summary="List quality rules",
)
async def list_rules(
    table_id: UUID | None = Query(None, description="按 table_id 过滤"),
    rule_type: str | None = Query(None, description="按 rule_type 过滤"),
    is_enabled: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> QualityRuleListResponse:
    stmt = select(QualityRule)
    if table_id:
        stmt = stmt.where(QualityRule.table_id == table_id)
    if rule_type:
        stmt = stmt.where(QualityRule.rule_type == rule_type)
    if is_enabled is not None:
        stmt = stmt.where(QualityRule.is_enabled == is_enabled)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = list(
        (
            await db.execute(
                stmt.order_by(QualityRule.created_at.desc()).limit(limit).offset(offset)
            )
        ).scalars()
    )
    return QualityRuleListResponse(
        items=[QualityRuleRead.model_validate(r) for r in rows],
        total=total,
    )


@router.post(
    "/rules",
    response_model=QualityRuleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a quality rule",
)
async def create_rule(
    payload: QualityRuleCreate,
    db: AsyncSession = Depends(get_db),
) -> QualityRule:
    table = await db.get(TableAsset, payload.table_id)
    if table is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Table not found")
    rule = QualityRule(
        table_id=payload.table_id,
        name=payload.name,
        rule_type=payload.rule_type,
        severity=payload.severity,
        definition=payload.definition,
        schedule=payload.schedule,
        description=payload.description,
    )
    db.add(rule)
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"rule '{payload.name}' already exists for this table",
        ) from e
    return rule


@router.delete(
    "/rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a quality rule",
)
async def delete_rule(rule_id: UUID, db: AsyncSession = Depends(get_db)) -> None:
    rule = await db.get(QualityRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    await db.delete(rule)
    await db.flush()


@router.get(
    "/top-low",
    summary="Tables with low health score (default < 70)",
)
async def top_low(
    threshold: float = Query(70.0, ge=0.0, le=100.0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = list(
        (
            await db.execute(
                select(TableAsset)
                .where(TableAsset.health_score.is_not(None), TableAsset.health_score < threshold)
                .order_by(TableAsset.health_score.asc())
                .limit(limit)
            )
        ).scalars()
    )
    return {
        "threshold": threshold,
        "items": [
            {
                "id": str(r.id),
                "fqn": r.fqn,
                "health_score": r.health_score,
                "tier": r.tier,
                "updated_at": r.health_score_updated_at.isoformat() if r.health_score_updated_at else None,
            }
            for r in rows
        ],
    }


@router.post(
    "/rules/{rule_id}/run",
    summary="Run a quality rule immediately (calls run_quality_check skill)",
)
async def run_rule(rule_id: UUID, db: AsyncSession = Depends(get_db)) -> dict:
    from idm_api.skills.runner import run_skill

    rule = await db.get(QualityRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    inputs = {"rule_id": str(rule.id), "apply": True}
    result = await run_skill("run_quality_check", inputs, db=db)
    return {
        "ok": result.ok,
        "rule_id": str(rule.id),
        "summary": result.output.summary,
        "items": result.output.items[:5],
        "error": result.error,
    }
