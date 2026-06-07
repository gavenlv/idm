"""/api/v1/tags: 业务标签字典 + 资产绑定."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.db import get_db
from idm_api.schemas import (
    TagBindRequest,
    TagCreate,
    TagListResponse,
    TagRead,
    TagUpdate,
)
from idm_kg.models.table_asset import TableAsset
from idm_kg.models.tag import AssetTag, Tag

router = APIRouter()


async def _with_counts(db: AsyncSession, rows: list[Tag]) -> list[TagRead]:
    if not rows:
        return []
    ids = [r.id for r in rows]
    counts = dict(
        (
            await db.execute(
                select(AssetTag.tag_id, func.count(AssetTag.table_id))
                .where(AssetTag.tag_id.in_(ids))
                .group_by(AssetTag.tag_id)
            )
        ).all()
    )
    out: list[TagRead] = []
    for r in rows:
        d = TagRead.model_validate(r).model_dump()
        d["asset_count"] = int(counts.get(r.id, 0))
        out.append(TagRead(**d))
    return out


@router.get("", response_model=TagListResponse, summary="List tags")
async def list_tags(
    category: str | None = Query(None, description="pii/tier/domain/status/custom"),
    q: str | None = Query(None, description="按 name 模糊查询"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> TagListResponse:
    stmt = select(Tag)
    if category:
        stmt = stmt.where(Tag.category == category)
    if q:
        stmt = stmt.where(Tag.name.ilike(f"%{q}%"))

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()
    rows = (
        (await db.execute(stmt.order_by(Tag.category, Tag.name).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    items = await _with_counts(db, rows)
    return TagListResponse(items=items, total=total)


@router.post("", response_model=TagRead, status_code=status.HTTP_201_CREATED, summary="Create a tag")
async def create_tag(
    payload: TagCreate,
    db: AsyncSession = Depends(get_db),
) -> TagRead:
    tag = Tag(
        name=payload.name,
        category=payload.category,
        color=payload.color,
        description=payload.description,
    )
    db.add(tag)
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"tag '{payload.name}' already exists") from e
    return (await _with_counts(db, [tag]))[0]


@router.patch("/{tag_id}", response_model=TagRead, summary="Update a tag")
async def update_tag(
    tag_id: UUID,
    payload: TagUpdate,
    db: AsyncSession = Depends(get_db),
) -> TagRead:
    tag = await db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")
    if payload.color is not None:
        tag.color = payload.color
    if payload.description is not None:
        tag.description = payload.description
    await db.flush()
    return (await _with_counts(db, [tag]))[0]


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a tag")
async def delete_tag(tag_id: UUID, db: AsyncSession = Depends(get_db)) -> None:
    tag = await db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")
    await db.delete(tag)
    await db.flush()


# === Asset binding ===
@router.post(
    "/assets/{table_id}/bind",
    response_model=dict,
    summary="Bind a tag to an asset",
)
async def bind_tag(
    table_id: UUID,
    payload: TagBindRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    table = await db.get(TableAsset, table_id)
    if table is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    tag = await db.get(Tag, payload.tag_id)
    if tag is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")
    # idempotent
    existing = (
        await db.execute(
            select(AssetTag).where(AssetTag.table_id == table_id, AssetTag.tag_id == payload.tag_id)
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(AssetTag(table_id=table_id, tag_id=payload.tag_id, source=payload.source))
        await db.flush()
    return {"table_id": str(table_id), "tag_id": str(payload.tag_id), "bound": True}


@router.post(
    "/assets/{table_id}/unbind",
    response_model=dict,
    summary="Unbind a tag from an asset",
)
async def unbind_tag(
    table_id: UUID,
    payload: TagBindRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    existing = (
        await db.execute(
            select(AssetTag).where(AssetTag.table_id == table_id, AssetTag.tag_id == payload.tag_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        await db.delete(existing)
        await db.flush()
    return {"table_id": str(table_id), "tag_id": str(payload.tag_id), "bound": False}


@router.get(
    "/assets/{table_id}",
    response_model=list[TagRead],
    summary="List tags of an asset",
)
async def list_asset_tags(
    table_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> list[TagRead]:
    rows = (
        (
            await db.execute(
                select(Tag)
                .join(AssetTag, AssetTag.tag_id == Tag.id)
                .where(AssetTag.table_id == table_id)
                .order_by(Tag.category, Tag.name)
            )
        )
        .scalars()
        .all()
    )
    return await _with_counts(db, rows)
