"""Pytest fixtures: in-memory SQLite 替代 PG, 跑 /health / /assets 基础用例。

M1: 用 SQLite + aiosqlite 做单测, 集成测试才用真实 PG。
"""
from __future__ import annotations

import os
import uuid as _uuid

# === 1) 在导入 app 前, 把 DB 切到 SQLite + 跳过 lifespan 里的 PG ping ===
os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
# 强制覆盖 .env 里的 APP_NAME, 让测试断言稳定
os.environ["APP_NAME"] = "idm-api"

from collections.abc import AsyncIterator  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID as PG_UUID  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.types import CHAR, TypeDecorator  # noqa: E402

# === 2) 替换 main 里的 engine 创建, 防止 lifespan 跑 PG ===
from idm_api import db as db_module  # noqa: E402

# 用一个临时文件 (避免 in-memory 跨连接不可见问题)
import tempfile as _tempfile
import os as _os

_TEST_DB_FILE = _os.path.join(_tempfile.gettempdir(), "idm_test.db")
# 删掉旧文件, 保证干净
try:
    _os.unlink(_TEST_DB_FILE)
except FileNotFoundError:
    pass
TEST_DB_URL = f"sqlite+aiosqlite:///{_TEST_DB_FILE}"

# === 3) 把 PG 专有类型在 sqlite 上 fallback, 避免 CompileError ===
from sqlalchemy.ext.compiler import compiles  # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb_to_json(element, compiler, **kw):  # noqa: ANN001
    return compiler.visit_JSON(element, **kw)


@compiles(INET, "sqlite")
def _inet_to_str(element, compiler, **kw):  # noqa: ANN001
    return "TEXT"


@compiles(ARRAY, "sqlite")
def _array_to_json(element, compiler, **kw):  # noqa: ANN001
    return compiler.visit_JSON(element, **kw)


# UUID 类型: 自定义 TypeDecorator, 同时处理 bind (UUID -> str) 和 result (str -> UUID).
# 替代直接的 PG_UUID(as_uuid=True), 让 sqlite 上 db.get / 查询都能正确转换.
from sqlalchemy.types import TypeDecorator, CHAR  # noqa: E402


class _IdmUUID(TypeDecorator):
    """跨 dialect 兼容的 UUID 类型.

    - PG: 用原生 UUID (as_uuid=True), 由 PG 处理转换
    - SQLite: 存 CHAR(36), Python 端做 str<->UUID 转换
    """

    impl = CHAR(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):  # noqa: ANN001
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value  # PG 原生处理
        return str(value)

    def process_result_value(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value  # PG 原生返回 UUID
        if isinstance(value, _uuid.UUID):
            return value
        return _uuid.UUID(str(value))


@pytest_asyncio.fixture
async def test_db_engine():
    """独立的 SQLite 文件 engine, 每次 fixture 重新建表.

    用文件而非 :memory: 是为了避免 in-memory 跨连接不可见问题.
    """
    from sqlalchemy import event

    engine = create_async_engine(
        TEST_DB_URL,
        echo=False,
        connect_args={"check_same_thread": False},
    )

    # 给 sqlite 注册 gen_random_uuid() -> Python uuid4
    @event.listens_for(engine.sync_engine, "connect")
    def _register_sqlite_uuid(dbapi_conn, _):  # noqa: ANN001
        try:
            dbapi_conn.create_function("gen_random_uuid", 0, lambda: str(_uuid.uuid4()))
        except Exception:
            pass

    yield engine
    await engine.dispose()
    # 测试结束后清理文件
    try:
        _os.unlink(_TEST_DB_FILE)
    except FileNotFoundError:
        pass


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
    from idm_kg.models.base import UUIDMixin
    from idm_api import db as db_module
    from sqlalchemy import Column

    # === 在 sqlite test 环境下, 把 PG_UUID(as_uuid=True) 全部替换为 _IdmUUID ===
    # 这样 db.get / where(id==) 都能正确处理 UUID <-> str 转换.
    from sqlalchemy.dialects.postgresql import UUID as _PGUUIDCls  # noqa: E402
    for table in Base.metadata.tables.values():
        for col in table.columns:
            col_type = col.type
            if isinstance(col_type, _PGUUIDCls):
                col.type = _IdmUUID()

    # 覆盖 get_engine / get_session_factory
    factory = async_sessionmaker(bind=test_db_engine, expire_on_commit=False, autoflush=False)

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

    app.dependency_overrides[db_module.get_db] = _override_session

    yield app

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(app_with_db) -> AsyncIterator[AsyncClient]:
    """httpx AsyncClient."""
    transport = ASGITransport(app=app_with_db)
    # reset feedback store 保证测试隔离
    from idm_api.eval.feedback import reset_feedback_store
    reset_feedback_store()
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    reset_feedback_store()


# ---------------------------------------------------------------------------
# BDD 同步 client (for pytest-bdd scenarios)
# ---------------------------------------------------------------------------

class _SyncClient:
    """Thin sync wrapper around httpx.AsyncClient + ASGI transport.

    pytest-bdd 生成的是 sync test function; 而 conftest 里的 `client` 是 async
    fixture. 这里提供一个 sync 入口, 把每个调用包到 `asyncio.run` 里。
    性能: BDD 用例数 < 30, 这个 wrapper 开销可忽略。
    """

    def __init__(self, app) -> None:
        from httpx import ASGITransport, AsyncClient
        self._app = app
        self._transport = ASGITransport(app=app)
        # 复用 feedback store reset
        from idm_api.eval.feedback import reset_feedback_store
        reset_feedback_store()
        self._reset_feedback = reset_feedback_store

    def _run(self, coro):
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            # 在已有 loop 里: 用 nest_asyncio 兜底 (仅测试用)
            import nest_asyncio  # type: ignore
            nest_asyncio.apply()
            return loop.run_until_complete(coro)
        return asyncio.run(coro)

    def _build(self) -> "AsyncClient":
        from httpx import AsyncClient
        return AsyncClient(transport=self._transport, base_url="http://test")

    def request(self, method: str, url: str, **kwargs):
        async def _do():
            async with self._build() as c:
                r = await c.request(method, url, **kwargs)
                # 立即读 body 以便 status_code / json() 可用
                await r.aread()
                return r
        return self._run(_do())

    def get(self, url: str, **kw):
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw):
        return self.request("POST", url, **kw)

    def patch(self, url: str, **kw):
        return self.request("PATCH", url, **kw)

    def delete(self, url: str, **kw):
        return self.request("DELETE", url, **kw)

    def close(self) -> None:
        self._reset_feedback()


@pytest.fixture
def bdd_client(app_with_db):
    """Sync HTTP client for BDD scenarios (pytest-bdd)."""
    c = _SyncClient(app_with_db)
    yield c
    c.close()
