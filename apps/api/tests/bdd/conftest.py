"""Local conftest for BDD tests.

The step definitions live under `tests/bdd/steps/`. We import them here
so that pytest-bdd registers all `@given` / `@when` / `@then` handlers
**before** it walks the auto-generated `test_*` functions from
`tests/bdd/test_*.py`.

Why this conftest is needed
---------------------------
pytest-bdd 8.x uses the `step` decorator which calls
`pytest.fixture(...)(func)` and stashes the resulting
`FixtureFunctionDefinition` into the **caller module's** `__dict__` via
`sys._getframe().f_locals`. In pytest 9, fixture discovery iterates
`dir(module)` for every collection node (the conftest, the test module),
but **does not** recurse into imported sub-modules.

If the step definitions live in `tests/bdd/steps/common_steps.py` and
are only imported here, the fixtures are correctly populated in
`common_steps.__dict__` but pytest's fixture manager never visits that
module, so it cannot resolve any step.

Two reliable workarounds are in this file:

1. We re-expose every `pytestbdd_stepdef_*` attribute from
   `common_steps` into this conftest's `__dict__`. This is a no-op
   for the user-defined steps but lets `parsefactories` see them when
   it walks the conftest module.
2. We install a `pytest_collection_modifyitems` hook that, after
   pytest has collected this directory, calls `parsefactories` on the
   steps module so any fixtures still missing end up registered.

ClickHouse & LLM mocking
------------------------
The BDD scenarios do not need a real ClickHouse / LLM. We patch
`idm_api.skills.runner.get_clickhouse_mcp` and
`idm_api.skills.runner.get_llm_router` for the duration of each test
so skill runs succeed even in CI without those services.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os as _os
import tempfile as _tempfile
import uuid as _uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# === 1) Register all step handlers via the side-effect import ===
from .steps import common_steps  # noqa: E401,E402

# === 2) Re-expose step fixtures in *this* module so parsefactories() sees them.
#        pytest walks `dir(conftest)` but does NOT walk into sub-modules.
for _k, _v in list(common_steps.__dict__.items()):
    if _k.startswith("pytestbdd_stepdef_"):
        globals()[_k] = _v
del _k, _v


def pytest_collection_modifyitems(config, items):  # type: ignore[no-untyped-def]
    """Last-resort hook: also call parsefactories on the steps module.

    pytest discovers fixtures per collection node (conftest, test file),
    and does not recurse into sub-modules. We force the fixture manager
    to walk `common_steps` as well, so every step fixture becomes
    available for scenario resolution.
    """
    fm = getattr(config, "_fixturemanager", None)
    if fm is None:
        fm = config.pluginmanager.get_plugin("funcmanage")
    if fm is None:
        return
    if common_steps in fm._holderobjseen:
        return
    try:
        fm.parsefactories(common_steps, nodeid="")
    except Exception:
        pass

    # Also explicitly register the step defs on each test module that
    # was collected. pytest-bdd stores the resolved step fixture name
    # in the test scenario as a fixture arg, and the fixture manager
    # only resolves those it knows about.
    for item in items:
        try:
            fm.parsefactories(common_steps, nodeid=item.nodeid)
        except Exception:
            pass

    # Diagnostic: report how many step defs are known
    step_count = 0
    for name, defs in fm._arg2fixturedefs.items():
        for fd in defs:
            if hasattr(fd.func, "_pytest_bdd_step_context"):
                step_count += 1
    print(f"\n[BDD] {step_count} step fixtures known to fixture manager\n")


# ---------------------------------------------------------------------------
# SQLite + PG type fallbacks (mirrors tests/conftest.py)
# ---------------------------------------------------------------------------


def _fresh_sqlite_url() -> str:
    path = _os.path.join(_tempfile.gettempdir(), f"idm_bdd_{_uuid.uuid4().hex}.db")
    try:
        _os.unlink(path)
    except FileNotFoundError:
        pass
    return f"sqlite+aiosqlite:///{path}"


def _patch_pg_types_for_sqlite() -> None:
    from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID as PG_UUID
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.types import CHAR, TypeDecorator

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

    from idm_kg import Base

    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, PG_UUID):
                col.type = _IdmUUID()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_app():
    """Create a fresh sqlite-backed FastAPI app and return (app, engine, db_url)."""
    _patch_pg_types_for_sqlite()
    _os.environ.setdefault("APP_ENV", "local")
    _os.environ["APP_NAME"] = "idm-api"

    db_url = _fresh_sqlite_url()
    engine = create_async_engine(
        db_url,
        echo=False,
        connect_args={"check_same_thread": False},
    )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    from idm_kg import Base

    @event.listens_for(engine.sync_engine, "connect")
    def _register_uuid(dbapi_conn, _):  # noqa: ANN001
        try:
            dbapi_conn.create_function("gen_random_uuid", 0, lambda: str(_uuid.uuid4()))
        except Exception:
            pass

    from idm_api import db as db_module

    async def _override_session():  # type: ignore[no-untyped-def]
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # Use asyncio.run for a clean loop — this fixture is called from
    # sync test functions, so we don't inherit any outer loop.
    asyncio.run(_setup())

    db_module._engine = engine
    db_module._session_factory = factory

    from idm_api.main import app

    app.dependency_overrides[db_module.get_db] = _override_session
    return app, engine, db_url


@pytest.fixture
def bdd_app():
    """Sync FastAPI app with sqlite + ClickHouse / LLM stubs.

    Mirrors `tests/conftest.py::app_with_db` but with mocked external
    services so BDD scenarios can run without a real ClickHouse.
    """
    app, engine, db_url = _build_app()

    fake_mcp = MagicMock()
    fake_mcp.list_databases.return_value = ["shop"]
    fake_mcp.list_tables.return_value = ["orders_daily"]
    fake_mcp.describe_table.return_value = []
    fake_mcp.health.return_value = {"status": "ok", "host": "fake-ch:18123"}

    fake_llm = MagicMock()

    async def _complete(*_args, **_kwargs):  # noqa: ANN001
        # When called with a JSON-mode prompt (e.g. nl2sql) we need a `sql`
        # field; for other prompts (e.g. map_glossary) we keep the legacy
        # `matches` shape. We detect by inspecting the user message: if it
        # contains the literal "sql" we return a SQL payload, otherwise
        # the glossary-style matches payload.
        try:
            msgs = _args[0] if _args else _kwargs.get("messages") or []
            text_blob = " ".join(
                str(m.get("content", "")) for m in msgs if isinstance(m, dict)
            ).lower()
        except Exception:
            text_blob = ""
        if "sql" in text_blob and "json" in text_blob:
            payload = {
                "sql": "SELECT count(*) AS row_count FROM shop.default.orders_daily",
                "explanation": "Count rows in the orders_daily table.",
            }
        elif "description" in text_blob:
            payload = {
                "description": "Aggregated daily orders with user, amount, and currency.",
                "tags": ["orders", "daily"],
            }
        elif "pii" in text_blob or "classify" in text_blob:
            payload = {
                "columns": [
                    {"name": "user_id", "pii_class": "pseudo", "confidence": 0.7},
                    {"name": "amount", "pii_class": "none", "confidence": 0.95},
                ]
            }
        elif "owner" in text_blob or "owners" in text_blob:
            payload = {
                "owners": [
                    {"user_email": "alice@example.com", "confidence": 0.85, "source": "git_blame"},
                ]
            }
        elif "lineage" in text_blob or "sql " in text_blob:
            payload = {
                "upstreams": [
                    {"upstream_fqn": "shop.default.orders_daily", "confidence": 1.0}
                ]
            }
        else:
            payload = {
                "matches": [{"term": "GMV", "confidence": 0.7, "reasoning": "test"}]
            }
        return {
            "content": json.dumps(payload),
            "model": "fake",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    fake_llm.complete = _complete
    fake_llm.last_model = "fake"

    p1 = patch("idm_api.skills.runner.get_clickhouse_mcp", return_value=fake_mcp)
    p2 = patch("idm_api.skills.runner.get_llm_router", return_value=fake_llm)
    p3 = patch("idm_api.routers.skills.get_clickhouse_mcp", return_value=fake_mcp)
    # Cover all builtin skills that import get_clickhouse_mcp directly
    # via `from idm_api.skills.mcp import get_clickhouse_mcp`. Each skill
    # module keeps its own reference, so we must patch the local symbol.
    # Only modules that actually have this attribute get patched; mock
    # errors out when patching a non-existent attribute.

    patches: list[Any] = []
    for mod_name in [
        "profiler",
        "run_quality_check",
        "detect_anomalies",
        "nl2sql",
        "discover_clickhouse_assets",
        "extract_sql_lineage",
        "lineage_reasoner",
        "map_glossary",
        "compose_insight",
        "infer_table_description",
        "infer_table_owners",
        "classify_pii_columns",
        "analyze_dbt_code",
        "parse_superset_dashboard",
        "parse_dbt_manifest",
        "parse_airflow_dag",
    ]:
        try:
            mod = importlib.import_module(f"idm_api.skills.builtin.{mod_name}")
        except Exception:
            continue
        if hasattr(mod, "get_clickhouse_mcp"):
            patches.append(
                patch.object(
                    mod, "get_clickhouse_mcp", return_value=fake_mcp
                )
            )

    p1.start()
    p2.start()
    p3.start()
    for p in patches:
        p.start()

    try:
        yield app
    finally:
        p1.stop()
        p2.stop()
        p3.stop()
        for p in patches:
            p.stop()
        app.dependency_overrides.clear()
        try:
            asyncio.run(engine.dispose())
        except Exception:
            pass
        try:
            path = db_url.replace("sqlite+aiosqlite:///", "")
            _os.unlink(path)
        except FileNotFoundError:
            pass


@pytest.fixture
def app_with_db(bdd_app):
    """Alias: step defs request `app_with_db` (defined in tests/conftest.py).

    BDD tests use the lighter-weight `bdd_app` instead.
    """
    return bdd_app


class _SyncClient:
    """Sync wrapper around httpx.AsyncClient (used by pytest-bdd steps)."""

    def __init__(self, app) -> None:
        self._app = app
        self._transport = ASGITransport(app=app)

    def _run(self, coro):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(coro)
        return asyncio.run(coro)

    def _build(self):
        return AsyncClient(transport=self._transport, base_url="http://test")

    def request(self, method, url, **kwargs):
        async def _do():
            async with self._build() as c:
                r = await c.request(method, url, **kwargs)
                await r.aread()
                return r
        return self._run(_do())

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def patch(self, url, **kw):
        return self.request("PATCH", url, **kw)

    def delete(self, url, **kw):
        return self.request("DELETE", url, **kw)


@pytest.fixture
def bdd_client(bdd_app):
    """Sync HTTP client for pytest-bdd scenarios."""
    return _SyncClient(bdd_app)
