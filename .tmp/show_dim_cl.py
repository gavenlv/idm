"""Show dim_users column lineage edges in detail."""
import asyncio
import sys
sys.path.insert(0, "apps/api/src")
sys.path.insert(0, "packages/kg/src")

from idm_api.db import get_session_factory
from sqlalchemy import select
from idm_kg.models.column_lineage import ColumnLineage
from idm_kg.models.table_asset import TableAsset
from idm_kg.models.column_asset import ColumnAsset


async def main():
    factory = get_session_factory()
    async with factory() as db:
        dim = (await db.execute(select(TableAsset).where(TableAsset.fqn.like("dbt-shop_dw.shop.default.dim_users")))).scalar_one()
        rows = (await db.execute(
            select(ColumnLineage).where(ColumnLineage.downstream_table_id == dim.id)
        )).scalars().all()
        print(f"=== {dim.fqn} column lineage ({len(rows)} edges) ===")
        for e in rows:
            up = await db.get(TableAsset, e.upstream_table_id)
            up_col = await db.get(ColumnAsset, e.upstream_column_id)
            down_col = await db.get(ColumnAsset, e.downstream_column_id)
            print(f"  {up.fqn}.{up_col.name} -> {down_col.name}")
            print(f"    type={e.transform_type}, expr={e.transform_expression!r}")
            print(f"    conf={e.confidence}, source={e.source}, component={e.component}")


asyncio.run(main())

