"""Pytest fixtures: in-memory SQLite 替代 PG, 跑 /health / /assets 基础用例。

M1: 用 SQLite + aiosqlite 做单测, 集成测试才用真实 PG。
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# === 1) 在导入 app 前, 把 DB 切到 SQLite + 跳过 lifespan 里的 PG ping ===
os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

# === 2) 替换 main 里的 engine 创建, 防止 lifespan 跑 PG ===
from idm_api import db as db_module  # noqa: E402

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def test_db_engine():
    """独立的 SQLite in-memory engine, 每次 fixture 新建."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def test_db_session(test_db_engine) -> AsyncIterator[AsyncSession]:
    """每个测试一个 session, 结束时回滚."""
    factory = async_sessionmaker(bind=test_db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def app_with_db(test_db_engine):
    """带测试 DB 的 FastAPI app."""
    from idm_kg import Base
    from idm_api import db as db_module

    # 覆盖 get_engine / get_session_factory
    factory = async_sessionmaker(bind=test_db_engine, expire_on_commit=False, autoflush=False)

    async def _override_engine():
        return test_db_engine

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # 建表
    async with test_db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # patch
    db_module._engine = test_db_engine
    db_module._session_factory = factory

    from idm_api.main import app  # noqa: PLC0415

    from fastapi import Depends  # noqa: PLC0415

    app.dependency_overrides[db_module.get_db] = _override_session

    yield app

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(app_with_db) -> AsyncIterator[AsyncClient]:
    """httpx AsyncClient."""
    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
