"""apps/api/scripts/start_local.py — 本地快速启动 API (SQLite, 0 依赖).

用法:
    python scripts/start_local.py
    # 或: IDM_DB_URL=sqlite+aiosqlite:///:memory: python scripts/start_local.py

适用:
    - 本地 demo / 演示 / 端到端验证 (无 PG / Docker)
    - CI 烟囱测试
    - 业务人员"我想看看 API 长啥样"

不适用:
    - 生产 (请用 PG)
    - BDD 测试 (已有专用 conftest)
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# 把 apps/api/src 加到 sys.path, 让 idm_api 可 import
API_SRC = Path(__file__).resolve().parent.parent / "src"
if str(API_SRC) not in sys.path:
    sys.path.insert(0, str(API_SRC))


async def _setup_db() -> None:
    """建 SQLite schema (替代 alembic upgrade head, 演示用)."""
    from idm_kg import Base
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    db_url = os.environ.get("IDM_DB_URL") or "sqlite+aiosqlite:////tmp/idm_local.db"
    print(f"[start_local] DB URL: {db_url}")

    # SQLite-friendly: 把 PG UUID/JSONB/ARRAY/INET 替换为 SQLite 兼容类型
    from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID as PG_UUID
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.types import CHAR, TypeDecorator
    import uuid as _uuid

    @compiles(JSONB, "sqlite")
    def _jsonb_to_json(element, compiler, **kw):  # noqa: ANN001
        return compiler.visit_JSON(element, **kw)

    @compiles(INET, "sqlite")
    def _inet_to_str(element, compiler, **kw):  # noqa: ANN001
        return "TEXT"

    @compiles(ARRAY, "sqlite")
    def _array_to_json(element, compiler, **kw):  # noqa: ANN001
        return compiler.visit_JSON(element, **kw)

    class _IdmUUID(TypeDecorator):
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
                return value
            return str(value)

        def process_result_value(self, value, dialect):  # noqa: ANN001
            if value is None:
                return None
            if dialect.name == "postgresql":
                return value
            if isinstance(value, _uuid.UUID):
                return value
            return _uuid.UUID(str(value))

    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, PG_UUID):
                col.type = _IdmUUID()

    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    print(f"[start_local] schema created ({len(Base.metadata.tables)} tables)")

    # 注入到 idm_api.db
    from idm_api import db as db_module
    db_module._engine = engine
    db_module._session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)


def main() -> int:
    asyncio.run(_setup_db())
    import uvicorn

    host = os.environ.get("API_HOST", "0.0.0.0")
    port = int(os.environ.get("API_PORT", "8080"))
    print(f"[start_local] starting uvicorn on {host}:{port}")
    print(f"[start_local] try: curl http://localhost:{port}/health/ready")
    uvicorn.run(
        "idm_api.main:app",
        host=host,
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
