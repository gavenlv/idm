"""lineage_to_column: 表级血缘边自动展开为列级 (M2.x 新增).

策略: 同名映射 (上游表 col X → 下游表 col X), transform_type=direct.
对没匹配上的下游列, 标记 transform_type=derivation.

幂等: ON CONFLICT (upstream_column_id, downstream_column_id, transform_type, job_id) DO NOTHING.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill
from idm_kg.models.column_asset import ColumnAsset
from idm_kg.models.column_lineage import ColumnLineage
from idm_kg.models.table_asset import TableAsset
from idm_kg.models.table_lineage import TableLineage

logger = logging.getLogger(__name__)


@skill(name="lineage_to_column", version=1, agent="lineage")
async def lineage_to_column(ctx: SkillContext, **inputs: Any) -> SkillResult:
    """把表级血缘边自动展开为列级 (同名映射).

    输入: 无 (用 KG 里所有 table_lineage)
    输出: column_lineage 边 (trans_type=direct by namematch)
    """
    apply: bool = bool(inputs.get("apply", True))
    min_confidence: float = float(inputs.get("min_confidence", 0.0))
    # 单边模式: 只跑这一条 table_lineage 边 (用于 bulk infer 的逐边调用)
    table_lineage_id: str | None = inputs.get("table_lineage_id")

    if ctx.db is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.db is None")

    # 1) 取表级血缘
    if table_lineage_id:
        from uuid import UUID
        try:
            edge_id = UUID(table_lineage_id)
        except (ValueError, TypeError):
            edge_id = None
        stmt = select(TableLineage)
        if edge_id is not None:
            stmt = stmt.where(TableLineage.id == edge_id)
    else:
        stmt = select(TableLineage)
    edges = list((await ctx.db.execute(stmt)).scalars())
    ctx.log("table_edges", count=len(edges))

    if not edges:
        return SkillResult(
            ok=True,
            output=SkillOutput(items=[], summary={"reason": "no table edges"}),
        )

    items: list[dict[str, Any]] = []
    n_direct = 0
    n_skipped = 0
    n_duplicated = 0

    for edge in edges:
        up = await ctx.db.get(TableAsset, edge.upstream_id)
        down = await ctx.db.get(TableAsset, edge.downstream_id)
        if up is None or down is None:
            n_skipped += 1
            continue

        up_cols = list(
            (await ctx.db.execute(select(ColumnAsset).where(ColumnAsset.table_id == up.id))).scalars()
        )
        down_cols = list(
            (await ctx.db.execute(select(ColumnAsset).where(ColumnAsset.table_id == down.id))).scalars()
        )
        if not up_cols or not down_cols:
            n_skipped += 1
            continue

        # 智能跳过: 如果 edge 有 SQL, 留给 infer_column_lineage 处理
        # (避免同名映射在 SQL 已知的情况下产生假阳性, 如 JOIN 中间表)
        if edge.sql:
            n_skipped += 1
            ctx.log("skip_namematch_has_sql", edge_id=str(edge.id))
            continue

        down_by_name = {c.name: c for c in down_cols}

        for up_col in up_cols:
            down_col = down_by_name.get(up_col.name)
            if down_col is None:
                continue
            # 置信度门槛 (bulk infer 可调)
            confidence = 0.7
            if confidence < min_confidence:
                continue
            # 去重: 如果 sqlglot 或更高置信度已存在这条 (up_col -> down_col) 边, 跳过
            existing = (
                await ctx.db.execute(
                    select(ColumnLineage).where(
                        ColumnLineage.upstream_column_id == up_col.id,
                        ColumnLineage.downstream_column_id == down_col.id,
                    )
                )
            ).scalars().all()
            if any((e.confidence or 0) > confidence for e in existing):
                n_duplicated += 1
                continue
            job_id = edge.job_id or "lineage_to_column"
            stmt_ins = (
                pg_insert(ColumnLineage)
                .values(
                    upstream_table_id=up.id,
                    downstream_table_id=down.id,
                    upstream_column_id=up_col.id,
                    downstream_column_id=down_col.id,
                    transform_type="direct",
                    transform_expression=up_col.name,
                    job_id=job_id,
                    component=edge.component or "lineage_to_column",
                    description=f"原样透传 {up_col.name} ({up_col.data_type})",
                    description_source="ai_inferred",
                    confidence=confidence,  # 同名映射不是 100% 准确
                    source="lineage_to_column",
                    pipeline_stage=edge.pipeline_stage,
                    extra={},
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        ColumnLineage.upstream_column_id,
                        ColumnLineage.downstream_column_id,
                        ColumnLineage.transform_type,
                        ColumnLineage.job_id,
                    ]
                )
                .returning(ColumnLineage.id)
            )
            r = await ctx.db.execute(stmt_ins)
            inserted = r.scalar_one_or_none()
            if inserted is None:
                n_duplicated += 1
            else:
                n_direct += 1
            items.append(
                {
                    "upstream_fqn": up.fqn,
                    "downstream_fqn": down.fqn,
                    "upstream_col": up_col.name,
                    "downstream_col": down_col.name,
                    "transform_type": "direct",
                }
            )

    if apply:
        await ctx.db.commit()
    else:
        await ctx.db.rollback()

    summary = {
        "table_edges_processed": len(edges),
        "column_edges_created": n_direct,
        "column_edges_skipped_dup": n_duplicated,
        "skipped": n_skipped,
    }
    return SkillResult(
        ok=True,
        output=SkillOutput(items=items, summary=summary),
    )
