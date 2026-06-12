"""/api/v1/descriptions: 资产/列/血缘 描述 (M2.x 新增).

操作:
- GET /api/v1/descriptions/coverage — 描述覆盖率
- PATCH /api/v1/assets/{id}/description — 更新表描述
- PATCH /api/v1/assets/{id}/columns/{col_id}/description — 更新列描述
- PATCH /api/v1/lineage/edges/{id}/description — 更新血缘边描述
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.db import get_db
from idm_api.schemas import AssetDescriptionCoverage, ColumnAssetRead, DescriptionUpdate, TableAssetRead
from idm_kg.models.column_asset import ColumnAsset
from idm_kg.models.column_lineage import ColumnLineage
from idm_kg.models.table_asset import TableAsset
from idm_kg.models.table_lineage import TableLineage

# description_router
description_router = APIRouter(prefix="/descriptions", tags=["descriptions"])

# asset_description_router (挂在 /assets 下)
asset_description_router = APIRouter(prefix="/assets", tags=["assets"])

# lineage_description_router (挂在 /lineage 下)
lineage_description_router = APIRouter(prefix="/lineage", tags=["lineage"])


# === Coverage ===
@description_router.get("/coverage", response_model=AssetDescriptionCoverage, summary="M2.x description coverage")
async def get_coverage(db: AsyncSession = Depends(get_db)) -> AssetDescriptionCoverage:
    """M2.x: 资产/列/血缘边 描述覆盖率 (验收指标)."""
    # Tables
    n_tbl = (await db.execute(select(func.count()).select_from(TableAsset))).scalar_one()
    n_tbl_desc = (await db.execute(
        select(func.count()).select_from(TableAsset).where(TableAsset.description.isnot(None))
    )).scalar_one()
    n_tbl_ai = (await db.execute(
        select(func.count()).select_from(TableAsset).where(TableAsset.description_source == "ai_inferred")
    )).scalar_one()
    n_tbl_manual = (await db.execute(
        select(func.count()).select_from(TableAsset).where(TableAsset.description_source == "manual")
    )).scalar_one()

    # Columns
    n_col = (await db.execute(select(func.count()).select_from(ColumnAsset))).scalar_one()
    n_col_desc = (await db.execute(
        select(func.count()).select_from(ColumnAsset).where(ColumnAsset.description.isnot(None))
    )).scalar_one()
    n_col_ai = (await db.execute(
        select(func.count()).select_from(ColumnAsset).where(ColumnAsset.description_source == "ai_inferred")
    )).scalar_one()
    n_col_manual = (await db.execute(
        select(func.count()).select_from(ColumnAsset).where(ColumnAsset.description_source == "manual")
    )).scalar_one()

    # Table Lineage
    n_tl = (await db.execute(select(func.count()).select_from(TableLineage))).scalar_one()
    n_tl_desc = (await db.execute(
        select(func.count()).select_from(TableLineage).where(TableLineage.description.isnot(None))
    )).scalar_one()

    # Column Lineage
    n_cl = (await db.execute(select(func.count()).select_from(ColumnLineage))).scalar_one()
    n_cl_desc = (await db.execute(
        select(func.count()).select_from(ColumnLineage).where(ColumnLineage.description.isnot(None))
    )).scalar_one()

    return AssetDescriptionCoverage(
        tables_total=n_tbl,
        tables_with_description=n_tbl_desc,
        tables_with_ai_description=n_tbl_ai,
        tables_with_manual_description=n_tbl_manual,
        columns_total=n_col,
        columns_with_description=n_col_desc,
        columns_with_ai_description=n_col_ai,
        columns_with_manual_description=n_col_manual,
        table_lineage_total=n_tl,
        table_lineage_with_description=n_tl_desc,
        column_lineage_total=n_cl,
        column_lineage_with_description=n_cl_desc,
        table_coverage_pct=round(100 * n_tbl_desc / n_tbl, 2) if n_tbl > 0 else 0,
        column_coverage_pct=round(100 * n_col_desc / n_col, 2) if n_col > 0 else 0,
        lineage_coverage_pct=round(100 * n_tl_desc / n_tl, 2) if n_tl > 0 else 0,
    )


# === Table description PATCH ===
@asset_description_router.patch("/{asset_id}/description", response_model=TableAssetRead, summary="Update asset description")
async def update_asset_description(
    asset_id: UUID,
    payload: DescriptionUpdate,
    db: AsyncSession = Depends(get_db),
) -> TableAssetRead:
    """更新表的 description. 人工编辑时 source=manual 覆写, AI 写入时 source=ai_inferred.

    铁律: 人工编辑 (source=manual) 后, 后续 AI 推断不会覆盖 (按 description_source 优先级).
    """
    asset = await db.get(TableAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    # 防 PII 真值: 简单正则
    import re
    phone_re = re.compile(r"1[3-9]\d{9}")
    id_card_re = re.compile(r"\d{17}[\dXx]")
    if phone_re.search(payload.description or "") or id_card_re.search(payload.description or ""):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="description 包含真实 PII (手机号/身份证号), 请改用 '11 位手机号' 等脱敏描述",
        )

    asset.description = payload.description
    asset.description_source = payload.source
    asset.description_rationale = payload.rationale
    asset.described_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(asset)
    return TableAssetRead.model_validate(asset)


# === Column description PATCH ===
@asset_description_router.patch("/{asset_id}/columns/{column_id}/description", response_model=ColumnAssetRead, summary="Update column description")
async def update_column_description(
    asset_id: UUID,
    column_id: UUID,
    payload: DescriptionUpdate,
    db: AsyncSession = Depends(get_db),
) -> ColumnAssetRead:
    """更新列的 description."""
    col = await db.get(ColumnAsset, column_id)
    if col is None or col.table_id != asset_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Column {column_id} not found in asset {asset_id}",
        )

    import re
    phone_re = re.compile(r"1[3-9]\d{9}")
    id_card_re = re.compile(r"\d{17}[\dXx]")
    if phone_re.search(payload.description or "") or id_card_re.search(payload.description or ""):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="description 包含真实 PII",
        )

    col.description = payload.description
    col.description_source = payload.source
    col.description_rationale = payload.rationale
    await db.flush()
    await db.refresh(col)
    return ColumnAssetRead.model_validate(col)


# === Lineage edge description PATCH ===
@lineage_description_router.patch("/edges/{edge_id}/description", summary="Update table lineage edge description")
async def update_lineage_edge_description(
    edge_id: UUID,
    payload: DescriptionUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """更新表级血缘边的 description (组件级)."""
    edge = await db.get(TableLineage, edge_id)
    if edge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Edge not found")

    edge.description = payload.description
    edge.description_source = payload.source
    edge.description_rationale = payload.rationale
    await db.flush()
    return {
        "id": str(edge.id),
        "upstream_id": str(edge.upstream_id),
        "downstream_id": str(edge.downstream_id),
        "description": edge.description,
        "description_source": edge.description_source,
        "description_rationale": edge.description_rationale,
    }


# === Lineage edges listing (filter by description) ===
@lineage_description_router.get("/edges", summary="List table lineage edges")
async def list_lineage_edges(
    with_description: bool | None = Query(None, description="True=有描述, False=无描述, None=全部"),
    component: str | None = Query(None, description="按 component 过滤"),
    pipeline_stage: int | None = Query(None, ge=1, le=6, description="按 6 阶段标号过滤"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """分页列出表级血缘边 (可按 description 过滤)."""
    stmt = select(TableLineage)
    if with_description is True:
        stmt = stmt.where(TableLineage.description.isnot(None))
    elif with_description is False:
        stmt = stmt.where(TableLineage.description.is_(None))
    if component:
        stmt = stmt.where(TableLineage.component == component)
    if pipeline_stage is not None:
        stmt = stmt.where(TableLineage.pipeline_stage == pipeline_stage)

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    stmt = stmt.order_by(TableLineage.created_at.desc()).limit(limit).offset(offset)
    rows = list((await db.execute(stmt)).scalars())

    # 展开 fqn
    table_ids = {e.upstream_id for e in rows} | {e.downstream_id for e in rows}
    fqn_map: dict[UUID, str] = {}
    if table_ids:
        fqn_rows = (
            await db.execute(select(TableAsset.id, TableAsset.fqn).where(TableAsset.id.in_(table_ids)))
        ).all()
        fqn_map = {r[0]: r[1] for r in fqn_rows}

    items = [
        {
            "id": str(e.id),
            "upstream_id": str(e.upstream_id),
            "downstream_id": str(e.downstream_id),
            "upstream_fqn": fqn_map.get(e.upstream_id),
            "downstream_fqn": fqn_map.get(e.downstream_id),
            "transform_type": e.transform_type,
            "transform_subtype": e.transform_subtype,
            "transform_expression": e.transform_expression,
            "component": e.component,
            "description": e.description,
            "description_source": e.description_source,
            "job_id": e.job_id,
            "confidence": e.confidence,
            "source": e.source,
            "pipeline_stage": e.pipeline_stage,
        }
        for e in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}
