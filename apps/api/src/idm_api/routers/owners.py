"""/api/v1/owners: 资产 Owner 列表 + 单条 verify 标记."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.db import get_db
from idm_api.schemas import AssetOwnerListResponse, AssetOwnerRead
from idm_kg.models.owner import AssetOwner
from idm_kg.models.table_asset import TableAsset

router = APIRouter()


@router.get("", response_model=AssetOwnerListResponse, summary="List asset owners")
async def list_owners(
    team: str | None = Query(None, description="按 team 过滤"),
    service: str | None = Query(None, description="按表 service 过滤 (fqn 前缀)"),
    role: str | None = Query(None, description="owner/steward/consumer"),
    verified: bool | None = Query(None, description="True=仅已 verify"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> AssetOwnerListResponse:
    """Owner 列表 (给 Owner 治理页面 + 告警用).

    支持 service / team / role / verified 过滤, 用于:
      - 列出 team 下所有未 verify 的 owner (待人工确认)
      - 列 service 内未分配 owner 的表 (告警: 数据资产无主)
    """
    stmt = select(AssetOwner, TableAsset.fqn).outerjoin(
        TableAsset, TableAsset.id == AssetOwner.table_id
    )
    if team:
        stmt = stmt.where(AssetOwner.team == team)
    if role:
        stmt = stmt.where(AssetOwner.role == role)
    if verified is not None:
        stmt = stmt.where(AssetOwner.is_verified.is_(verified))
    if service:
        stmt = stmt.where(TableAsset.fqn.like(f"{service}.%"))

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()
    rows = (await db.execute(stmt.order_by(AssetOwner.team, AssetOwner.user_email).limit(limit).offset(offset))).all()

    items: list[AssetOwnerRead] = []
    for row, fqn in rows:
        d = AssetOwnerRead.model_validate(row).model_dump()
        d["table_fqn"] = fqn
        items.append(AssetOwnerRead(**d))
    return AssetOwnerListResponse(items=items, total=total)


@router.post("/{owner_id}/verify", response_model=AssetOwnerRead, summary="Mark owner as verified")
async def verify_owner(
    owner_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> AssetOwner:
    """人工确认 (is_verified=True)."""
    owner = await db.get(AssetOwner, owner_id)
    if owner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Owner not found")
    owner.is_verified = True
    await db.commit()
    await db.refresh(owner)
    return owner


@router.delete("/{owner_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an owner")
async def delete_owner(
    owner_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """删除一条 owner 记录 (误判时人工清理)."""
    owner = await db.get(AssetOwner, owner_id)
    if owner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Owner not found")
    await db.delete(owner)
    await db.flush()
