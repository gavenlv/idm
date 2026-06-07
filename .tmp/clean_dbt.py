"""清理 dbt 资产 + lineage"""
import asyncio, sys
sys.path.insert(0, 'd:/workspace/github-ai/idm/apps/api/src')
sys.path.insert(0, 'd:/workspace/github-ai/idm/packages/kg/src')
from sqlalchemy import delete, select
from idm_api.db import get_session_factory
from idm_kg.models.table_asset import TableAsset
from idm_kg.models.table_lineage import TableLineage
from idm_kg.models.service import Service

async def main():
    sf = get_session_factory()
    async with sf() as s:
        sv = (await s.execute(select(Service).where(Service.name == 'dbt-shop_dw'))).scalar_one_or_none()
        if sv:
            n1 = (await s.execute(delete(TableLineage).where(TableLineage.source == 'dbt_manifest'))).rowcount
            n2 = (await s.execute(delete(TableAsset).where(TableAsset.fqn.like('dbt-shop_dw.%')))).rowcount
            await s.commit()
            print(f"cleaned: lineage={n1}, assets={n2}")
        else:
            print("no service")

asyncio.run(main())
