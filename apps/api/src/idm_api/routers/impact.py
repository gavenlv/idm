"""/api/v1/impact: 表影响分析 (基于 table_lineage BFS).

M3 入口. 流程:
  GET /api/v1/impact/{table_id}?direction=both&depth=3
    -> 复用 /assets/{id}/lineage 的 BFS
    -> 收集受影响的 owner / term
    -> 返回 paths 列表 (供 UI 高亮)
"""
from __future__ import annotations

from collections import deque
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.db import get_db
from idm_api.schemas import ImpactAnalysisResponse
from idm_kg.models.glossary import AssetTerm
from idm_kg.models.owner import AssetOwner
from idm_kg.models.table_asset import TableAsset
from idm_kg.models.table_lineage import TableLineage

router = APIRouter()


@router.get(
    "/{table_id}",
    response_model=ImpactAnalysisResponse,
    summary="Impact analysis of an asset (BFS upstream + downstream)",
)
async def impact(
    table_id: UUID,
    direction: str = Query("both", pattern="^(upstream|downstream|both)$"),
    depth: int = Query(3, ge=1, le=10),
    db: AsyncSession = Depends(get_db),
) -> ImpactAnalysisResponse:
    """BFS 上游/下游, 汇总受影响 owner / term."""
    asset = await db.get(TableAsset, table_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    visited: set[UUID] = {asset.id}
    paths: list[dict] = []
    upstream_set: set[UUID] = set()
    downstream_set: set[UUID] = set()

    # 上游 BFS
    if direction in ("upstream", "both"):
        frontier: deque[UUID] = deque([asset.id])
        for _ in range(depth):
            if not frontier:
                break
            edges = list(
                (
                    await db.execute(
                        select(TableLineage).where(TableLineage.downstream_id.in_(list(frontier)))
                    )
                ).scalars()
            )
            next_frontier: deque[UUID] = deque()
            for e in edges:
                paths.append({"from": str(e.upstream_id), "to": str(e.downstream_id), "via": e.transform_type, "src": e.source})
                if e.upstream_id not in visited:
                    visited.add(e.upstream_id)
                    upstream_set.add(e.upstream_id)
                    next_frontier.append(e.upstream_id)
            frontier = next_frontier

    # 下游 BFS
    if direction in ("downstream", "both"):
        frontier = deque([asset.id])
        for _ in range(depth):
            if not frontier:
                break
            edges = list(
                (
                    await db.execute(
                        select(TableLineage).where(TableLineage.upstream_id.in_(list(frontier)))
                    )
                ).scalars()
            )
            next_frontier: deque[UUID] = deque()
            for e in edges:
                paths.append({"from": str(e.upstream_id), "to": str(e.downstream_id), "via": e.transform_type, "src": e.source})
                if e.downstream_id not in visited:
                    visited.add(e.downstream_id)
                    downstream_set.add(e.downstream_id)
                    next_frontier.append(e.downstream_id)
            frontier = next_frontier

    affected_ids = upstream_set | downstream_set

    # 拉 owner / term
    affected_owners: list[str] = []
    affected_terms: list[str] = []
    if affected_ids:
        # owners: 找 verified owner
        owners = list(
            (
                await db.execute(
                    select(AssetOwner.user_email).where(
                        AssetOwner.table_id.in_(list(affected_ids)),
                        AssetOwner.is_verified.is_(True),
                    )
                )
            ).scalars()
        )
        affected_owners = sorted(set(owners))

        # terms: 找 asset_term 关联的 term.name
        term_ids = list(
            (
                await db.execute(
                    select(AssetTerm.term_id).where(AssetTerm.table_id.in_(list(affected_ids)))
                )
            ).scalars()
        )
        if term_ids:
            from idm_kg.models.glossary import GlossaryTerm

            term_names = list(
                (
                    await db.execute(
                        select(GlossaryTerm.name).where(GlossaryTerm.id.in_(term_ids))
                    )
                ).scalars()
            )
            affected_terms = sorted(set(term_names))

    # 补 fqn 路径
    fqn_map: dict[UUID, str] = {}
    if affected_ids:
        rows = (
            await db.execute(
                select(TableAsset.id, TableAsset.fqn).where(TableAsset.id.in_(list(affected_ids)))
            )
        ).all()
        fqn_map = {r[0]: r[1] for r in rows}
    for p in paths:
        try:
            f = UUID(p["from"])
            t = UUID(p["to"])
        except (ValueError, TypeError):
            continue
        p["from_fqn"] = fqn_map.get(f)
        p["to_fqn"] = fqn_map.get(t)

    return ImpactAnalysisResponse(
        center_fqn=asset.fqn,
        center_id=asset.id,
        direction=direction,  # type: ignore[arg-type]
        depth=depth,
        upstream_count=len(upstream_set),
        downstream_count=len(downstream_set),
        affected_owners=affected_owners,
        affected_terms=affected_terms,
        paths=paths,
    )
