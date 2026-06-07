"""/api/v1/glossary: 业务术语字典 + 资产绑定."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.db import get_db
from idm_api.schemas import (
    GlossaryBindRequest,
    GlossaryListResponse,
    GlossaryTermCreate,
    GlossaryTermRead,
    GlossaryTermUpdate,
)
from idm_kg.models.glossary import AssetTerm, GlossaryTerm
from idm_kg.models.table_asset import TableAsset

router = APIRouter()


async def _with_counts(db: AsyncSession, rows: list[GlossaryTerm]) -> list[GlossaryTermRead]:
    if not rows:
        return []
    ids = [r.id for r in rows]
    counts = dict(
        (
            await db.execute(
                select(AssetTerm.term_id, func.count(AssetTerm.table_id))
                .where(AssetTerm.term_id.in_(ids))
                .group_by(AssetTerm.term_id)
            )
        ).all()
    )
    out: list[GlossaryTermRead] = []
    for r in rows:
        d = GlossaryTermRead.model_validate(r).model_dump()
        d["asset_count"] = int(counts.get(r.id, 0))
        out.append(GlossaryTermRead(**d))
    return out


@router.get("", response_model=GlossaryListResponse, summary="List glossary terms")
async def list_terms(
    q: str | None = Query(None, description="模糊查询 name / definition / synonyms"),
    domain: str | None = Query(None, description="按 domain 过滤"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> GlossaryListResponse:
    stmt = select(GlossaryTerm)
    if domain:
        stmt = stmt.where(GlossaryTerm.domain == domain)
    if q:
        like = f"%{q}%"
        # 任何字段命中即可 (简化: name/definition)
        stmt = stmt.where(GlossaryTerm.name.ilike(like) | GlossaryTerm.definition.ilike(like))

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()
    rows = (
        (await db.execute(stmt.order_by(GlossaryTerm.name).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    items = await _with_counts(db, rows)
    return GlossaryListResponse(items=items, total=total)


@router.post(
    "",
    response_model=GlossaryTermRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a glossary term",
)
async def create_term(
    payload: GlossaryTermCreate,
    db: AsyncSession = Depends(get_db),
) -> GlossaryTermRead:
    term = GlossaryTerm(
        name=payload.name,
        definition=payload.definition,
        domain=payload.domain,
        owner_team=payload.owner_team,
        synonyms=payload.synonyms,
    )
    db.add(term)
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"term '{payload.name}' already exists") from e
    return (await _with_counts(db, [term]))[0]


@router.patch("/{term_id}", response_model=GlossaryTermRead, summary="Update a glossary term")
async def update_term(
    term_id: UUID,
    payload: GlossaryTermUpdate,
    db: AsyncSession = Depends(get_db),
) -> GlossaryTermRead:
    term = await db.get(GlossaryTerm, term_id)
    if term is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Term not found")
    if payload.definition is not None:
        term.definition = payload.definition
    if payload.domain is not None:
        term.domain = payload.domain
    if payload.owner_team is not None:
        term.owner_team = payload.owner_team
    if payload.synonyms is not None:
        term.synonyms = payload.synonyms
    await db.flush()
    return (await _with_counts(db, [term]))[0]


@router.delete("/{term_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a glossary term")
async def delete_term(term_id: UUID, db: AsyncSession = Depends(get_db)) -> None:
    term = await db.get(GlossaryTerm, term_id)
    if term is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Term not found")
    await db.delete(term)
    await db.flush()


# === Asset binding ===
@router.post(
    "/assets/{table_id}/bind",
    response_model=dict,
    summary="Bind a glossary term to an asset",
)
async def bind_term(
    table_id: UUID,
    payload: GlossaryBindRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    table = await db.get(TableAsset, table_id)
    if table is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    term = await db.get(GlossaryTerm, payload.term_id)
    if term is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Term not found")
    existing = (
        await db.execute(
            select(AssetTerm).where(AssetTerm.table_id == table_id, AssetTerm.term_id == payload.term_id)
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            AssetTerm(
                table_id=table_id,
                term_id=payload.term_id,
                confidence=payload.confidence,
                source=payload.source,
            )
        )
        await db.flush()
    return {"table_id": str(table_id), "term_id": str(payload.term_id), "bound": True}


@router.post(
    "/assets/{table_id}/unbind",
    response_model=dict,
    summary="Unbind a glossary term from an asset",
)
async def unbind_term(
    table_id: UUID,
    payload: GlossaryBindRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    existing = (
        await db.execute(
            select(AssetTerm).where(AssetTerm.table_id == table_id, AssetTerm.term_id == payload.term_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        await db.delete(existing)
        await db.flush()
    return {"table_id": str(table_id), "term_id": str(payload.term_id), "bound": False}


@router.get(
    "/assets/{table_id}",
    response_model=list[GlossaryTermRead],
    summary="List glossary terms of an asset",
)
async def list_asset_terms(
    table_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> list[GlossaryTermRead]:
    rows = (
        (
            await db.execute(
                select(GlossaryTerm)
                .join(AssetTerm, AssetTerm.term_id == GlossaryTerm.id)
                .where(AssetTerm.table_id == table_id)
                .order_by(GlossaryTerm.name)
            )
        )
        .scalars()
        .all()
    )
    return await _with_counts(db, rows)
