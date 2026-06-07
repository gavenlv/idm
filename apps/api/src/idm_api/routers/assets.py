"""/api/v1/assets: 资产 CRUD (table_asset)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.db import get_db
from idm_api.schemas import (
    TableAssetCreate,
    TableAssetListResponse,
    TableAssetRead,
)
from idm_kg.models.table_asset import TableAsset

router = APIRouter()


@router.get("", response_model=TableAssetListResponse, summary="List table assets")
async def list_assets(
    q: str | None = Query(None, description="按 name 或 fqn 模糊匹配"),
    tier: str | None = Query(None, description="按 tier 过滤"),
    service: str | None = Query(None, description="按 fqn 前缀 service 过滤"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> TableAssetListResponse:
    """分页查询资产 (M1 简化, M1 S1.3 加全文搜索)."""
    base = select(TableAsset)
    if q:
        like = f"%{q.lower()}%"
        base = base.where(
            (func.lower(TableAsset.name).like(like)) | (func.lower(TableAsset.fqn).like(like))
        )
    if tier:
        base = base.where(TableAsset.tier == tier)
    if service:
        base = base.where(TableAsset.fqn.like(f"{service}.%"))

    # total
    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    # items
    stmt = base.order_by(TableAsset.fqn).limit(limit).offset(offset)
    items = list((await db.execute(stmt)).scalars().all())

    return TableAssetListResponse(
        items=[TableAssetRead.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=TableAssetRead, status_code=status.HTTP_201_CREATED, summary="Create asset")
async def create_asset(
    payload: TableAssetCreate,
    db: AsyncSession = Depends(get_db),
) -> TableAsset:
    """手动登记一个资产 (M1 临时, M1 S1.2 后由 Skill 写入)."""
    # FQN 唯一性
    existing = await db.execute(select(TableAsset).where(TableAsset.fqn == payload.fqn))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Asset fqn '{payload.fqn}' already exists",
        )

    data = payload.model_dump()
    asset = TableAsset(**data)
    db.add(asset)
    await db.flush()
    return asset


@router.get("/{asset_id}", response_model=TableAssetRead, summary="Get asset")
async def get_asset(asset_id: UUID, db: AsyncSession = Depends(get_db)) -> TableAsset:
    """获取单个资产。"""
    asset = await db.get(TableAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    return asset
