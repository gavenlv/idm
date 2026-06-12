"""Run infer_column_lineage on all dim_users table_lineage edges."""
import asyncio
import sys
sys.path.insert(0, "apps/api/src")
sys.path.insert(0, "packages/kg/src")

from idm_api.db import get_session_factory
from sqlalchemy import select, or_, delete
from idm_kg.models.table_lineage import TableLineage
from idm_kg.models.table_asset import TableAsset
from idm_kg.models.column_lineage import ColumnLineage
from idm_kg.models.column_asset import ColumnAsset
from idm_api.skills.builtin.infer_column_lineage import infer_column_lineage
from idm_api.skills.registry import SkillContext


async def main():
    factory = get_session_factory()
    async with factory() as db:
        dim = (await db.execute(select(TableAsset).where(TableAsset.fqn.like("dbt-shop_dw.shop.default.dim_users")))).scalar_one()
        # 先清掉 dim_users 的 column_lineage 边
        await db.execute(delete(ColumnLineage).where(ColumnLineage.downstream_table_id == dim.id))
        await db.commit()
        print(f"cleared dim_users column_lineage edges")

        edges = list((await db.execute(
            select(TableLineage).where(
                or_(TableLineage.upstream_id == dim.id, TableLineage.downstream_id == dim.id),
                TableLineage.transform_type == "dbt_model"
            )
        )).scalars())
        # 提前取 fqn 避免 inner skill commit 后 lazy-load
        edge_meta = []
        for e in edges:
            up = await db.get(TableAsset, e.upstream_id)
            down = await db.get(TableAsset, e.downstream_id)
            edge_meta.append((str(e.id), up.fqn, down.fqn))

        print(f"=== Running infer_column_lineage on {len(edge_meta)} edges ===")
        for eid, up_fqn, down_fqn in edge_meta:
            ctx = SkillContext(db=db, dry_run=False)
            res = await infer_column_lineage(
                ctx,
                use_case_id=None,
                table_lineage_id=eid,
                apply=True,
            )
            print(f"  {up_fqn} -> {down_fqn}: created={res.output.summary.get('column_edges_created', 0)}")
        await db.commit()

        # 现在看 dim_users 覆盖
        rows = (await db.execute(
            select(ColumnLineage).where(ColumnLineage.downstream_table_id == dim.id)
        )).scalars().all()
        print(f"\n=== After: {len(rows)} column_lineage edges for dim_users ===")
        for e in rows:
            up = await db.get(TableAsset, e.upstream_table_id)
            up_col = await db.get(ColumnAsset, e.upstream_column_id)
            down_col = await db.get(ColumnAsset, e.downstream_column_id)
            print(f"  {up.fqn}.{up_col.name} -> {down_col.name} (transform={e.transform_type})")


asyncio.run(main())
