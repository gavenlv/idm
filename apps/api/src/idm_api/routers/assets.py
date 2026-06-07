"""/api/v1/assets: 资产 CRUD (table_asset)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.db import get_db
from idm_api.schemas import (
    AssetPiiSummary,
    ColumnAssetListResponse,
    ColumnAssetRead,
    LineageEdgeRead,
    LineageGraphResponse,
    TableAssetCreate,
    TableAssetListResponse,
    TableAssetRead,
)
from idm_kg.models.column_asset import ColumnAsset
from idm_kg.models.table_asset import TableAsset
from idm_kg.models.table_lineage import TableLineage

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


@router.get("/{asset_id}/columns", response_model=ColumnAssetListResponse, summary="List columns of asset")
async def list_asset_columns(
    asset_id: UUID,
    pii_only: bool = Query(False, description="仅返回 pii_class != none 的列"),
    db: AsyncSession = Depends(get_db),
) -> ColumnAssetListResponse:
    """列出某张表的所有列 (M1 S1.5: 给详情页 + PII 高亮用).

    返回列按 ordinal 升序; pii_only=True 时只返回有 PII 风险的列.
    """
    asset = await db.get(TableAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    stmt = select(ColumnAsset).where(ColumnAsset.table_id == asset_id)
    if pii_only:
        stmt = stmt.where(ColumnAsset.pii_class != "none")
    stmt = stmt.order_by(ColumnAsset.ordinal)
    cols = list((await db.execute(stmt)).scalars().all())
    return ColumnAssetListResponse(
        items=[ColumnAssetRead.model_validate(c) for c in cols],
        total=len(cols),
    )


@router.get("/{asset_id}/pii-summary", response_model=AssetPiiSummary, summary="Asset PII summary")
async def asset_pii_summary(asset_id: UUID, db: AsyncSession = Depends(get_db)) -> AssetPiiSummary:
    """一张表的 PII 风险摘要: 各类计数 + 高风险列样例.

    用途: 资产详情页顶部"合规"卡片.
    """
    asset = await db.get(TableAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    cols = list(
        (
            await db.execute(
                select(ColumnAsset).where(
                    ColumnAsset.table_id == asset_id, ColumnAsset.pii_class != "none"
                )
            )
        ).scalars()
    )

    by_class: dict[str, int] = {}
    samples: list[dict] = []
    high_risk = 0
    for c in cols:
        by_class[c.pii_class] = by_class.get(c.pii_class, 0) + 1
        samples.append(
            {
                "column_name": c.name,
                "pii_class": c.pii_class,
                "confidence": c.pii_confidence,
            }
        )
        if c.pii_class in ("id_card", "card_full", "ssn", "passport", "phone", "email", "address"):
            high_risk += 1

    return AssetPiiSummary(
        table_id=asset_id,
        pii_columns=len(cols),
        high_risk_columns=high_risk,
        by_class=by_class,
        samples=sorted(samples, key=lambda x: -x["confidence"])[:20],
    )


@router.get("/{asset_id}/lineage", response_model=LineageGraphResponse, summary="Asset lineage graph")
async def asset_lineage(
    asset_id: UUID,
    depth: int = Query(3, ge=1, le=10, description="BFS 上/下游层数"),
    db: AsyncSession = Depends(get_db),
) -> LineageGraphResponse:
    """以某表为中心的 lineage BFS (双向), 返回 nodes + edges."""
    asset = await db.get(TableAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    # BFS 上下游 (用 SQL 一次性取出 depth 范围内所有边, 再去重 nodes)
    visited_ids: set[UUID] = {asset.id}
    up_edges: list[LineageEdgeRead] = []
    down_edges: list[LineageEdgeRead] = []
    frontier_up: set[UUID] = {asset.id}
    frontier_down: set[UUID] = {asset.id}

    for _ in range(depth):
        if not frontier_up and not frontier_down:
            break
        # 上游 (upstream of current frontier)
        if frontier_up:
            up_rows = list(
                (
                    await db.execute(
                        select(TableLineage).where(TableLineage.downstream_id.in_(frontier_up))
                    )
                ).scalars()
            )
            new_up: set[UUID] = set()
            for e in up_rows:
                up_edges.append(e)
                if e.upstream_id not in visited_ids:
                    visited_ids.add(e.upstream_id)
                    new_up.add(e.upstream_id)
            frontier_up = new_up
        # 下游
        if frontier_down:
            down_rows = list(
                (
                    await db.execute(
                        select(TableLineage).where(TableLineage.upstream_id.in_(frontier_down))
                    )
                ).scalars()
            )
            new_down: set[UUID] = set()
            for e in down_rows:
                down_edges.append(e)
                if e.downstream_id not in visited_ids:
                    visited_ids.add(e.downstream_id)
                    new_down.add(e.downstream_id)
            frontier_down = new_down

    # 拉所有 nodes 的 fqn
    nodes: list[dict] = []
    if visited_ids:
        rows = (
            await db.execute(select(TableAsset.id, TableAsset.fqn, TableAsset.asset_type, TableAsset.tier, TableAsset.name).where(TableAsset.id.in_(visited_ids)))
        ).all()
        for r in rows:
            nodes.append({"id": str(r[0]), "fqn": r[1], "asset_type": r[2], "tier": r[3], "name": r[4]})

    # edges 补 fqn
    all_edges = up_edges + down_edges
    edge_ids = set()
    for e in all_edges:
        edge_ids.add(e.upstream_id)
        edge_ids.add(e.downstream_id)
    fqn_map: dict[UUID, str] = {}
    if edge_ids:
        fqn_rows = (
            await db.execute(select(TableAsset.id, TableAsset.fqn).where(TableAsset.id.in_(edge_ids)))
        ).all()
        fqn_map = {r[0]: r[1] for r in fqn_rows}

    def to_read(e: TableLineage) -> LineageEdgeRead:
        return LineageEdgeRead(
            id=e.id,
            upstream_id=e.upstream_id,
            downstream_id=e.downstream_id,
            transform_type=e.transform_type,
            job_id=e.job_id,
            confidence=e.confidence,
            source=e.source,
            upstream_fqn=fqn_map.get(e.upstream_id),
            downstream_fqn=fqn_map.get(e.downstream_id),
        )

    up_reads = [to_read(e) for e in up_edges]
    down_reads = [to_read(e) for e in down_edges]

    return LineageGraphResponse(
        center_fqn=asset.fqn,
        center_id=asset.id,
        upstream=up_reads,
        downstream=down_reads,
        nodes=nodes,
        edges=up_reads + down_reads,
    )
