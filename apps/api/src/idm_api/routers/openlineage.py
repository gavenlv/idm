"""/api/v1/lineage/openlineage: OpenLineage 兼容端点 (M2.5 新增).

设计: docs/design/openlineage-alignment.md

参考: https://openlineage.io/

提供 2 个端点:
  - GET  /api/v1/lineage/openlineage/event/{event_id}  - 导出单事件为 OpenLineage JSON
  - GET  /api/v1/lineage/openlineage/events            - 列出最近事件
  - GET  /api/v1/lineage/openlineage/export/{run_id}   - 翻译某 pipeline_run 为 OL 事件
  - POST /api/v1/lineage/openlineage/ingest            - 接受外部 OL 事件 (Marquez 等推送)
"""
from __future__ import annotations

import logging
import uuid
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.db import get_db
from idm_api.schemas import OpenLineageEventRead
from idm_kg.models.lineage_event import LineageEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lineage/openlineage", tags=["lineage-openlineage"])


@router.get("/event/{event_id}", response_model=OpenLineageEventRead, summary="Get OL event by id")
async def get_openlineage_event(
    event_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> OpenLineageEventRead:
    """导出单个 lineage_event 为 OpenLineage RunEvent JSON."""
    try:
        eid = uuid.UUID(str(event_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid event_id")

    event = await db.get(LineageEvent, eid)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="event not found")

    return OpenLineageEventRead.model_validate(_to_read(event))


@router.get("/events", response_model=list[OpenLineageEventRead], summary="List recent OL events")
async def list_openlineage_events(
    limit: int = Query(50, ge=1, le=500, description="max events to return"),
    job_namespace: str | None = Query(None, description="filter by job.namespace"),
    job_name: str | None = Query(None, description="filter by job.name"),
    run_id: str | None = Query(None, description="filter by run.runId"),
    db: AsyncSession = Depends(get_db),
) -> list[OpenLineageEventRead]:
    """列出最近 N 个 lineage_event, 支持 job/run 过滤."""
    stmt = select(LineageEvent).order_by(desc(LineageEvent.event_time)).limit(limit)
    if job_namespace:
        stmt = stmt.where(LineageEvent.job_namespace == job_namespace)
    if job_name:
        stmt = stmt.where(LineageEvent.job_name == job_name)
    if run_id:
        stmt = stmt.where(LineageEvent.run_id == run_id)
    rows = list((await db.execute(stmt)).scalars())
    return [OpenLineageEventRead.model_validate(_to_read(e)) for e in rows]


@router.get("/export/{pipeline_run_id}", summary="Export pipeline_run as OpenLineage event")
async def export_pipeline_run(
    pipeline_run_id: str,
    event_type: str = Query("COMPLETE", description="START | RUNNING | COMPLETE | FAIL | ABORT"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """翻译某次 pipeline_run 为 OpenLineage RunEvent JSON.

    复用 emit_openlineage_event skill 的逻辑, 但直接返回 OL JSON (不写库)。
    """
    from idm_api.skills.builtin.emit_openlineage_event import emit_openlineage_event
    from idm_api.skills.registry import SkillContext

    ctx = SkillContext(db=db, use_case_id=None, dry_run=True)  # dry_run: 不写库
    result = await emit_openlineage_event(
        ctx,
        pipeline_run_id=pipeline_run_id,
        event_type=event_type,
        dry_run=True,
    )
    if not result.ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND
            if "not found" in (result.error or "")
            else status.HTTP_400_BAD_REQUEST,
            detail=result.error or "skill failed",
        )
    items = result.output.items
    if not items:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no lineage event produced (no recent edges?)",
        )
    return items[0]


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED, summary="Ingest external OL event")
async def ingest_openlineage_event(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """接受外部 OpenLineage 事件 (Marquez / DataHub / Airflow OL plugin 推送).

    校验 eventType / job / run 必填, 写 lineage_event 表 (审计)。
    不修改 IDM 内部表 (table_lineage / column_lineage) — 那是另一个映射流程。
    """
    body: dict[str, Any] = await request.json()
    event_type = body.get("eventType", "").upper()
    if event_type not in {"START", "RUNNING", "COMPLETE", "FAIL", "ABORT"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid eventType: {event_type}",
        )
    job = body.get("job", {})
    run = body.get("run", {})
    if not (job.get("namespace") and job.get("name") and run.get("runId")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing required: job.namespace, job.name, run.runId",
        )

    from datetime import datetime

    event_time_str = body.get("eventTime")
    event_time = (
        datetime.fromisoformat(event_time_str.replace("Z", "+00:00"))
        if event_time_str
        else datetime.utcnow()
    )

    event_row = LineageEvent(
        event_type=event_type,
        event_time=event_time,
        job_namespace=job["namespace"],
        job_name=job["name"],
        run_id=run["runId"],
        inputs=body.get("inputs", []),
        outputs=body.get("outputs", []),
        facets=body.get("runFacets", {}),
        producer=body.get("producer", "external"),
        source_skill="ingest_openlineage_event",
        extra={
            "schemaURL": body.get("schemaURL"),
            "jobFacets": body.get("jobFacets", {}),
        },
    )
    db.add(event_row)
    await db.commit()
    await db.refresh(event_row)

    return {
        "ok": True,
        "event_id": str(event_row.id),
        "event_type": event_type,
        "job": {"namespace": job["namespace"], "name": job["name"]},
        "run_id": run["runId"],
    }


def _to_read(e: LineageEvent) -> dict[str, Any]:
    """把 ORM 行转 Pydantic dict (含 OL RunEvent JSON)."""
    return {
        "id": e.id,
        "event_type": e.event_type,
        "event_time": e.event_time,
        "job_namespace": e.job_namespace,
        "job_name": e.job_name,
        "run_id": e.run_id,
        "inputs": e.inputs or [],
        "outputs": e.outputs or [],
        "facets": e.facets or {},
        "producer": e.producer,
        "source_skill": e.source_skill,
        "pipeline_run_id": e.pipeline_run_id,
        "ol_run_event": {
            "eventType": e.event_type,
            "eventTime": e.event_time.isoformat() if e.event_time else None,
            "producer": e.producer or "",
            "schemaURL": "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent",
            "job": {"namespace": e.job_namespace, "name": e.job_name},
            "run": {"runId": e.run_id},
            "inputs": e.inputs or [],
            "outputs": e.outputs or [],
            "runFacets": e.facets or {},
        },
        "created_at": e.created_at,
        "updated_at": e.updated_at,
    }
