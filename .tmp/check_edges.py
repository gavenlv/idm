"""Check column lineage edges per table."""
import asyncio
import sys
sys.path.insert(0, "apps/api/src")
sys.path.insert(0, "packages/kg/src")

from idm_api.db import get_session_factory
from sqlalchemy import select, func
from idm_kg.models.column_lineage import ColumnLineage
from idm_kg.models.table_asset import TableAsset


async def main():
    factory = get_session_factory()
    async with factory() as db:
        rows = (await db.execute(
            select(
                TableAsset.fqn,
                func.count(ColumnLineage.id).label("n_edges"),
            )
            .join(ColumnLineage, ColumnLineage.downstream_table_id == TableAsset.id)
            .group_by(TableAsset.id, TableAsset.fqn)
            .order_by(func.count(ColumnLineage.id).desc())
        )).all()
        print("=== Column lineage edges by downstream table ===")
        for fqn, n in rows:
            print(f"  {fqn}: {n} edges")
        n_total = (await db.execute(select(func.count()).select_from(ColumnLineage))).scalar_one()
        print(f"\nTotal: {n_total}")


asyncio.run(main())
