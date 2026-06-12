"""Run infer_column_lineage on dim_users table_lineage edges."""
import asyncio
import sys
sys.path.insert(0, "apps/api/src")
sys.path.insert(0, "packages/kg/src")

from idm_api.db import get_session_factory
from sqlalchemy import select, or_
from idm_kg.models.table_lineage import TableLineage
from idm_kg.models.table_asset import TableAsset
from idm_api.skills.builtin.infer_column_lineage import infer_column_lineage
from idm_api.skills.registry import SkillContext


async def main():
    factory = get_session_factory()
    async with factory() as db:
        dim = (await db.execute(select(TableAsset).where(TableAsset.fqn.like("dbt-shop_dw.shop.default.dim_users")))).scalar_one()
        edges = list((await db.execute(
            select(TableLineage).where(
                or_(TableLineage.upstream_id == dim.id, TableLineage.downstream_id == dim.id),
                TableLineage.transform_type == "dbt_model"
            )
        )).scalars())
        print(f"=== dim_users dbt_model edges: {len(edges)} ===")
        for e in edges:
            up = await db.get(TableAsset, e.upstream_id)
            down = await db.get(TableAsset, e.downstream_id)
            print(f"  {up.fqn} -> {down.fqn}")
            print(f"    sql (first 300 chars): {(e.sql or '')[:300]}")
            print(f"    sql is None: {e.sql is None}")
            print(f"    sql length: {len(e.sql) if e.sql else 0}")

        # Now run infer_column_lineage
        print("\n=== Running infer_column_lineage on first edge ===")
        if edges:
            ctx = SkillContext(db=db, dry_run=False)
            res = await infer_column_lineage(
                ctx,
                use_case_id=None,
                table_lineage_id=str(edges[0].id),
            )
            print(f"OK={res.ok}")
            print(f"summary: {res.output.summary}")
            print(f"items: {len(res.output.items)}")
            for item in res.output.items[:5]:
                print(f"  {item}")


asyncio.run(main())
