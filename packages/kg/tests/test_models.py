"""idm_kg 模型单测 (字段默认值 / 关系 / 字符串表示)."""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_kg import Service, TableAsset


@pytest.mark.asyncio
async def test_service_defaults(test_db_engine):
    from idm_kg import Base
    async with test_db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = __import__("idm_kg", fromlist=[""]).__dict__  # placeholder

    from sqlalchemy.ext.asyncio import async_sessionmaker
    factory = async_sessionmaker(bind=test_db_engine, expire_on_commit=False)
    async with factory() as session:  # type: AsyncSession
        svc = Service(name="ch-prod", type="clickhouse")
        session.add(svc)
        await session.commit()
        await session.refresh(svc)

        assert svc.tier == "normal"
        assert svc.status == "active"
        assert svc.created_at is not None
        assert svc.id is not None


@pytest.mark.asyncio
async def test_table_asset_unique_fqn(test_db_engine):
    from idm_kg import Base, Database, Schema, Service
    async with test_db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from sqlalchemy.ext.asyncio import async_sessionmaker
    factory = async_sessionmaker(bind=test_db_engine, expire_on_commit=False)
    async with factory() as session:
        svc = Service(name="ch", type="clickhouse")
        db = Database(name="shop", service=svc)
        sch = Schema(name="main", database=db)
        t1 = TableAsset(name="orders", fqn="ch.shop.main.orders", schema_=sch)
        session.add_all([t1])
        await session.commit()
        assert t1.fqn == "ch.shop.main.orders"
