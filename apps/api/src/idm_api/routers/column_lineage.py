"""/api/v1/lineage/column: 列级血缘 (M2.x 新增)."""
from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.db import get_db
from idm_api.schemas import (
    BulkInferRequest,
    BulkInferResponse,
    ColumnCoverageEntry,
    ColumnCoverageResponse,
    ColumnLineageEdgeRead,
    ColumnLineageResponse,
    ColumnLineageStatsResponse,
    LineageEdgeRead,
    LineageGraphResponse,
    TableColumnCoverage,
)
from idm_kg.models.column_asset import ColumnAsset
from idm_kg.models.column_lineage import ColumnLineage
from idm_kg.models.table_asset import TableAsset
from idm_kg.models.table_lineage import TableLineage

router = APIRouter(prefix="/lineage/column", tags=["lineage"])


def _to_read(e: ColumnLineage, up_col: ColumnAsset | None, down_col: ColumnAsset | None,
             up_table: TableAsset | None, down_table: TableAsset | None) -> ColumnLineageEdgeRead:
    return ColumnLineageEdgeRead(
        id=e.id,
        upstream_table_id=e.upstream_table_id,
        downstream_table_id=e.downstream_table_id,
        upstream_column_id=e.upstream_column_id,
        downstream_column_id=e.downstream_column_id,
        transform_type=e.transform_type,
        transform_expression=e.transform_expression,
        job_id=e.job_id,
        component=e.component,
        description=e.description,
        description_source=e.description_source,
        confidence=e.confidence,
        source=e.source,
        pipeline_stage=e.pipeline_stage,
        upstream_table_fqn=up_table.fqn if up_table else None,
        downstream_table_fqn=down_table.fqn if down_table else None,
        upstream_column_name=up_col.name if up_col else None,
        downstream_column_name=down_col.name if down_col else None,
        upstream_column_type=up_col.data_type if up_col else None,
        downstream_column_type=down_col.data_type if down_col else None,
    )


@router.get("/stats", response_model=ColumnLineageStatsResponse, summary="Column lineage stats")
async def column_lineage_stats(
    db: AsyncSession = Depends(get_db),
) -> ColumnLineageStatsResponse:
    """M2.x: 列级血缘统计 + 描述覆盖."""
    n_edges = (await db.execute(select(func.count()).select_from(ColumnLineage))).scalar_one()

    # transform_type 分布
    tt_rows = (
        await db.execute(
            select(ColumnLineage.transform_type, func.count())
            .group_by(ColumnLineage.transform_type)
        )
    ).all()
    n_transform_types = {r[0]: r[1] for r in tt_rows}

    # component 分布
    comp_rows = (
        await db.execute(
            select(ColumnLineage.component, func.count())
            .group_by(ColumnLineage.component)
        )
    ).all()
    n_components = {r[0]: r[1] for r in comp_rows}

    # coverage: 有列级血缘的表数 / 列数
    n_tables_with_col = (
        await db.execute(
            select(func.count(func.distinct(ColumnLineage.downstream_table_id)))
        )
    ).scalar_one()

    n_tables_total = (await db.execute(select(func.count()).select_from(TableAsset))).scalar_one()

    coverage = {
        "tables_with_col_lineage": n_tables_with_col,
        "tables_total": n_tables_total,
    }

    return ColumnLineageStatsResponse(
        n_edges=n_edges,
        n_transform_types=n_transform_types,
        n_components=n_components,
        coverage=coverage,
    )


@router.get("/table/{table_id}", response_model=ColumnLineageResponse, summary="Column lineage by table")
async def column_lineage_by_table(
    table_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ColumnLineageResponse:
    """列出某表的所有列级血缘 (上 + 下游)."""
    asset = await db.get(TableAsset, table_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Table not found")

    up_stmt = select(ColumnLineage).where(ColumnLineage.downstream_table_id == table_id)
    down_stmt = select(ColumnLineage).where(ColumnLineage.upstream_table_id == table_id)

    up_rows = list((await db.execute(up_stmt)).scalars())
    down_rows = list((await db.execute(down_stmt)).scalars())

    # 展开 column/table 信息 (注意: up/down rows 都要展开 up + down 双方的 col/table)
    all_col_ids: set[UUID] = set()
    all_table_ids: set[UUID] = set()
    for e in up_rows:
        all_col_ids.add(e.upstream_column_id)
        all_col_ids.add(e.downstream_column_id)
        all_table_ids.add(e.upstream_table_id)
        all_table_ids.add(e.downstream_table_id)
    for e in down_rows:
        all_col_ids.add(e.upstream_column_id)
        all_col_ids.add(e.downstream_column_id)
        all_table_ids.add(e.upstream_table_id)
        all_table_ids.add(e.downstream_table_id)

    col_by_id: dict[UUID, ColumnAsset] = {}
    if all_col_ids:
        cols = list(
            (await db.execute(select(ColumnAsset).where(ColumnAsset.id.in_(all_col_ids)))).scalars()
        )
        col_by_id = {c.id: c for c in cols}

    table_by_id: dict[UUID, TableAsset] = {}
    if all_table_ids:
        tbls = list(
            (await db.execute(select(TableAsset).where(TableAsset.id.in_(all_table_ids)))).scalars()
        )
        table_by_id = {t.id: t for t in tbls}

    up_edges = [
        _to_read(e, col_by_id.get(e.upstream_column_id), col_by_id.get(e.downstream_column_id),
                 table_by_id.get(e.upstream_table_id), table_by_id.get(e.downstream_table_id))
        for e in up_rows
    ]
    down_edges = [
        _to_read(e, col_by_id.get(e.upstream_column_id), col_by_id.get(e.downstream_column_id),
                 table_by_id.get(e.upstream_table_id), table_by_id.get(e.downstream_table_id))
        for e in down_rows
    ]

    return ColumnLineageResponse(
        center_table_id=table_id,
        upstream=up_edges,
        downstream=down_edges,
        total=len(up_edges) + len(down_edges),
    )


@router.get("/table/{table_id}/{column_name:path}", response_model=ColumnLineageResponse, summary="Column lineage by column name")
async def column_lineage_by_column(
    table_id: UUID,
    column_name: str,
    db: AsyncSession = Depends(get_db),
) -> ColumnLineageResponse:
    """按列名查列级血缘 (上 + 下游)."""
    col_stmt = select(ColumnAsset).where(
        ColumnAsset.table_id == table_id, ColumnAsset.name == column_name
    )
    col = (await db.execute(col_stmt)).scalar_one_or_none()
    if col is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Column '{column_name}' not found in table"
        )

    up_stmt = select(ColumnLineage).where(ColumnLineage.downstream_column_id == col.id)
    down_stmt = select(ColumnLineage).where(ColumnLineage.upstream_column_id == col.id)

    up_rows = list((await db.execute(up_stmt)).scalars())
    down_rows = list((await db.execute(down_stmt)).scalars())

    all_col_ids: set[UUID] = set()
    all_table_ids: set[UUID] = set()
    for e in up_rows:
        all_col_ids.add(e.upstream_column_id)
        all_col_ids.add(e.downstream_column_id)
        all_table_ids.add(e.upstream_table_id)
        all_table_ids.add(e.downstream_table_id)
    for e in down_rows:
        all_col_ids.add(e.upstream_column_id)
        all_col_ids.add(e.downstream_column_id)
        all_table_ids.add(e.upstream_table_id)
        all_table_ids.add(e.downstream_table_id)

    col_by_id: dict[UUID, ColumnAsset] = {}
    if all_col_ids:
        cols = list(
            (await db.execute(select(ColumnAsset).where(ColumnAsset.id.in_(all_col_ids)))).scalars()
        )
        col_by_id = {c.id: c for c in cols}
    table_by_id: dict[UUID, TableAsset] = {}
    if all_table_ids:
        tbls = list(
            (await db.execute(select(TableAsset).where(TableAsset.id.in_(all_table_ids)))).scalars()
        )
        table_by_id = {t.id: t for t in tbls}

    up_edges = [
        _to_read(e, col_by_id.get(e.upstream_column_id), col_by_id.get(e.downstream_column_id),
                 table_by_id.get(e.upstream_table_id), table_by_id.get(e.downstream_table_id))
        for e in up_rows
    ]
    down_edges = [
        _to_read(e, col_by_id.get(e.upstream_column_id), col_by_id.get(e.downstream_column_id),
                 table_by_id.get(e.upstream_table_id), table_by_id.get(e.downstream_table_id))
        for e in down_rows
    ]

    return ColumnLineageResponse(
        center_table_id=table_id,
        center_column_id=col.id,
        upstream=up_edges,
        downstream=down_edges,
        total=len(up_edges) + len(down_edges),
    )


# === Column Lineage Coverage (M2.5+) ===

@router.get(
    "/coverage",
    response_model=ColumnCoverageResponse,
    summary="Column lineage coverage matrix (OpenLineage-style)",
)
async def column_lineage_coverage(
    db: AsyncSession = Depends(get_db),
    only_with_table_lineage: bool = Query(
        False, description="Filter: only tables that already have at least 1 table_lineage edge"
    ),
) -> ColumnCoverageResponse:
    """全表列血缘覆盖矩阵 (OpenLineage/Marquez-style coverage report).

    返回每张表的:
      - column 数量
      - 有 lineage 的 column 数量
      - 覆盖率 % (有 lineage 的 column / total column)
      - 每列的 upstream/downstream 状态
    """
    # 1) 读所有 table
    tables = list((await db.execute(select(TableAsset))).scalars())
    if only_with_table_lineage:
        # 找出有 table_lineage 边的 table_id
        edges = list(
            (
                await db.execute(
                    select(TableLineage.upstream_id, TableLineage.downstream_id)
                )
            ).all()
        )
        has_tl_ids: set[UUID] = set()
        for u, d in edges:
            has_tl_ids.add(u)
            has_tl_ids.add(d)
        tables = [t for t in tables if t.id in has_tl_ids]

    if not tables:
        return ColumnCoverageResponse(
            total_tables=0,
            total_columns=0,
            total_columns_with_lineage=0,
            overall_coverage_pct=0.0,
            tables=[],
        )

    # 2) 一次性读所有 column
    table_ids = [t.id for t in tables]
    cols = list(
        (await db.execute(select(ColumnAsset).where(ColumnAsset.table_id.in_(table_ids)))).scalars()
    )
    cols_by_table: dict[UUID, list[ColumnAsset]] = {}
    for c in cols:
        cols_by_table.setdefault(c.table_id, []).append(c)

    # 3) 一次性读所有 column_lineage
    col_ids = [c.id for c in cols]
    cl_edges = list(
        (
            await db.execute(
                select(
                    ColumnLineage.upstream_column_id,
                    ColumnLineage.downstream_column_id,
                ).where(
                    ColumnLineage.upstream_column_id.in_(col_ids)
                    | ColumnLineage.downstream_column_id.in_(col_ids)
                )
            )
        ).all()
    )
    # 列 -> 该列的上游边数 / 下游边数
    # down_id 是某列的下游, 所以该列有 1 条"派生到 down_id"的边 → 该列的 downstream 计数 +1
    # up_id 是某列的上游, 所以该列有 1 条"从 up_id 派生来"的边 → 该列的 upstream 计数 +1
    n_up: dict[UUID, int] = {}  # column_id -> n_upstream_edges (该列被多少上游派生)
    n_down: dict[UUID, int] = {}  # column_id -> n_downstream_edges (该列派生出多少下游)
    for up_id, down_id in cl_edges:
        n_up[down_id] = n_up.get(down_id, 0) + 1
        n_down[up_id] = n_down.get(up_id, 0) + 1

    # 4) 一次性读 table_lineage (只 count)
    tl_count_stmt = select(
        TableLineage.upstream_id,
        TableLineage.downstream_id,
    )
    tl_rows = list((await db.execute(tl_count_stmt)).all())
    tl_count_by_table: dict[UUID, int] = {}
    for u, d in tl_rows:
        tl_count_by_table[u] = tl_count_by_table.get(u, 0) + 1
        tl_count_by_table[d] = tl_count_by_table.get(d, 0) + 1

    # 5) 组装响应
    out: list[TableColumnCoverage] = []
    total_columns = 0
    total_with_lineage = 0
    for t in tables:
        t_cols = cols_by_table.get(t.id, [])
        entries: list[ColumnCoverageEntry] = []
        n_with = 0
        for c in t_cols:
            up_n = n_up.get(c.id, 0)
            down_n = n_down.get(c.id, 0)
            if up_n > 0 or down_n > 0:
                n_with += 1
            entries.append(
                ColumnCoverageEntry(
                    column_id=c.id,
                    column_name=c.name,
                    data_type=c.data_type,
                    has_upstream=up_n > 0,
                    has_downstream=down_n > 0,
                    n_upstream_edges=up_n,
                    n_downstream_edges=down_n,
                )
            )
        n = len(t_cols)
        n_tl = tl_count_by_table.get(t.id, 0)
        # === 源表特殊处理 ===
        # 源表 (无任何 table_lineage 边) = 纯 passthrough, 100% 覆盖 (设计上无上游)
        # 派生表有 table_lineage 边时, 才按"有列级血缘的列数 / 总列数"算覆盖率
        is_source_table = n_tl == 0
        if is_source_table:
            n_with = n  # 源表所有列视为"已覆盖" (passthrough)
            coverage_pct = 100.0
        else:
            coverage_pct = round((n_with / n) * 100, 1) if n > 0 else 0.0
        total_columns += n
        total_with_lineage += n_with
        out.append(
            TableColumnCoverage(
                table_id=t.id,
                table_fqn=t.fqn,
                asset_type=t.asset_type,
                tier=t.tier,
                n_columns=n,
                n_columns_with_lineage=n_with,
                coverage_pct=coverage_pct,
                has_table_lineage=n_tl > 0,
                n_table_lineage_edges=n_tl,
                columns=entries,
            )
        )

    overall_pct = round((total_with_lineage / total_columns) * 100, 1) if total_columns > 0 else 0.0
    return ColumnCoverageResponse(
        total_tables=len(tables),
        total_columns=total_columns,
        total_columns_with_lineage=total_with_lineage,
        overall_coverage_pct=overall_pct,
        tables=out,
    )


@router.post(
    "/infer-all",
    response_model=BulkInferResponse,
    summary="Bulk infer column lineage for all (or selected) tables",
)
async def bulk_infer_column_lineage(
    payload: BulkInferRequest = Body(default=BulkInferRequest()),
    db: AsyncSession = Depends(get_db),
) -> BulkInferResponse:
    """批量推断列血缘 — 让"所有表都能达到列级血缘"。

    流程 (按需):
      1) `lineage_reasoner`         — 对没有 table_lineage 的表, 先建立表级边
      2) `infer_column_lineage`     — SQL parser 静态推断 (100% 准确)
      3) `lineage_to_column`        — 表级边 → 列级映射 (80% 命中)

    入参: BulkInferRequest (table_ids=None = 全表, dry_run=True = 不写库)
    """
    from datetime import datetime, timezone

    from idm_api.skills.builtin.infer_column_lineage import infer_column_lineage
    from idm_api.skills.builtin.lineage_reasoner import lineage_reasoner
    from idm_api.skills.builtin.lineage_to_column import lineage_to_column
    from idm_api.skills.registry import SkillContext

    started_at = datetime.now(timezone.utc)
    t0 = time.time()

    # === 1. 选表 ===
    if payload.table_ids:
        tables = list(
            (
                await db.execute(
                    select(TableAsset).where(TableAsset.id.in_(payload.table_ids))
                )
            ).scalars()
        )
    else:
        tables = list((await db.execute(select(TableAsset))).scalars())

    # 记录起始 column_lineage 边数
    n_cl_start = (
        await db.execute(select(func.count()).select_from(ColumnLineage))
    ).scalar_one()
    n_tl_start = (
        await db.execute(select(func.count()).select_from(TableLineage))
    ).scalar_one()

    tables_processed = 0
    tables_skipped = 0
    errors: list[str] = []
    skill_summary: dict[str, Any] = {}

    # === 2. 三步推断 ===
    # 提前取 id/fqn 避免 lazy-load 在异常处理路径触发 MissingGreenlet
    # 一次性 (id, fqn) tuple 化, 避免 session 中其他查询导致 ORM 实例过期
    table_pairs: list[tuple[Any, str]] = [(t.id, t.fqn) for t in tables]
    for t_id, t_fqn in table_pairs:
        try:
            # Step 1: 表级血缘 reasoner (建立表级边, 让 lineage_to_column 有原料)
            if payload.include_table_lineage_inference:
                ctx = SkillContext(db=db, dry_run=payload.dry_run)
                res = await lineage_reasoner(
                    ctx,
                    use_case_id=None,
                    target_table_id=str(t_id),
                )
                if res.ok:
                    skill_summary["lineage_reasoner"] = (
                        skill_summary.get("lineage_reasoner", 0) + 1
                    )

            # Step 2 + 3: 拿该表的所有 table_lineage 边, 逐边跑 infer_column_lineage + lineage_to_column
            edges = list(
                (
                    await db.execute(
                        select(TableLineage).where(
                            (TableLineage.upstream_id == t_id)
                            | (TableLineage.downstream_id == t_id)
                        )
                    )
                ).scalars()
            )
            # 提前取 id, 避免 inner skill commit/rollback 之后 e.id lazy-load
            edge_ids = [str(e.id) for e in edges]
            for eid in edge_ids:
                # Step 2: infer_column_lineage (SQL parser, 静态 100%)
                if payload.include_column_lineage_inference:
                    ctx = SkillContext(db=db, dry_run=payload.dry_run)
                    res = await infer_column_lineage(
                        ctx,
                        use_case_id=None,
                        table_lineage_id=eid,
                        apply=not payload.dry_run,
                    )
                    if res.ok:
                        skill_summary["infer_column_lineage"] = (
                            skill_summary.get("infer_column_lineage", 0) + 1
                        )

                # Step 3: lineage_to_column (表级 → 列级, 同名映射兜底)
                if payload.include_lineage_to_column:
                    ctx = SkillContext(db=db, dry_run=payload.dry_run)
                    res = await lineage_to_column(
                        ctx,
                        use_case_id=None,
                        table_lineage_id=eid,
                        min_confidence=payload.min_confidence,
                        apply=not payload.dry_run,
                    )
                    if res.ok:
                        skill_summary["lineage_to_column"] = (
                            skill_summary.get("lineage_to_column", 0) + 1
                        )

            tables_processed += 1
        except Exception as ex:
            tables_skipped += 1
            errors.append(f"{t_fqn}: {ex!r}")

    # === 3. 统计 ===
    if not payload.dry_run:
        await db.commit()

    n_cl_end = (
        await db.execute(select(func.count()).select_from(ColumnLineage))
    ).scalar_one()
    n_tl_end = (
        await db.execute(select(func.count()).select_from(TableLineage))
    ).scalar_one()

    finished_at = datetime.now(timezone.utc)
    return BulkInferResponse(
        ok=len(errors) == 0,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=int((time.time() - t0) * 1000),
        tables_processed=tables_processed,
        tables_skipped=tables_skipped,
        table_lineage_edges_created=n_tl_end - n_tl_start,
        column_lineage_edges_created=n_cl_end - n_cl_start,
        errors=errors,
        dry_run=payload.dry_run,
        summary={
            "skill_calls": skill_summary,
            "table_count": len(tables),
        },
    )
