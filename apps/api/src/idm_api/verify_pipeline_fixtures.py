"""verify_pipeline_fixtures.py — 端到端验证 6 阶段 fixtures 能正确加载到 IDM 知识图谱.

跑法:
    cd apps/api && uv run --no-progress python -m idm_api.verify_pipeline_fixtures

设计:
    - 用 SQLite 内存库 (无需 PG)
    - Mock ClickHouse (无需真实 CH)
    - Mock Superset (无需真实 SS)
    - 6 个阶段全部跑一遍, 统计写入 / 失败
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[4]  # /idm  (file 在 idm/apps/api/src/idm_api/)
FIXTURE_DIR = ROOT / "fixtures" / "pipeline-demo"
GCS_ROOT = FIXTURE_DIR / "gcs"
GH_ROOT = FIXTURE_DIR / "github"

# === 在 import 任何 idm_api 模块前, 先把 fixture 路径塞进 env ===
os.environ.setdefault("MOCK_GCS_ROOT", str(GCS_ROOT))
os.environ.setdefault("MOCK_GITHUB_ROOT", str(GH_ROOT))
os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("APP_NAME", "idm-api")

# === SQLite in-memory + 替换 asyncpg 引擎 ===
SQLITE_PATH = os.path.join(tempfile.gettempdir(), f"idm_verify_{uuid.uuid4().hex}.db")
SQLITE_URL = f"sqlite+aiosqlite:///{SQLITE_PATH}"
os.environ["DATABASE_URL"] = SQLITE_URL
os.environ["DATABASE_URL_SYNC"] = SQLITE_URL.replace("aiosqlite", "sqlite")

from sqlalchemy import event  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from idm_kg import Base  # noqa: E402
from idm_api.skills import builtin  # noqa: E402,F401  (触发 @skill 注册)


def _patch_pg_types_for_sqlite() -> None:
    """把 PG-only 类型 (UUID, JSONB, INET, ARRAY) 降级到 SQLite."""
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
            if isinstance(value, uuid.UUID):
                return value
            return uuid.UUID(str(value))

    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, PG_UUID):
                col.type = _IdmUUID()


_patch_pg_types_for_sqlite()

# === Mock ClickHouse & Superset (无需真实服务) ===
_fake_ch = MagicMock()
_fake_ch.health.return_value = {"status": "ok", "host": "mock", "mode": "mock"}
_fake_ch.list_databases.return_value = ["shop"]
_fake_ch.list_tables.return_value = ["fct_orders_daily", "fct_orders_risk_daily"]
# 模拟两张表的列定义 (供 stage 5 ClickHouse 发现) — 按真实 ClickHouse 协议返回 dict
def _fake_describe_table(database, tname):
    cols = {
        "fct_orders_risk_daily": [
            {"name": "order_id", "type": "String", "default_type": "", "comment": ""},
            {"name": "user_id", "type": "UInt64", "default_type": "", "comment": ""},
            {"name": "order_date", "type": "Date", "default_type": "", "comment": ""},
            {"name": "risk_score", "type": "Float64", "default_type": "", "comment": ""},
            {"name": "risk_label", "type": "String", "default_type": "", "comment": ""},
            {"name": "fraud_prob", "type": "Float64", "default_type": "", "comment": ""},
            {"name": "chargeback_prob", "type": "Float64", "default_type": "", "comment": ""},
            {"name": "model_version", "type": "String", "default_type": "", "comment": ""},
            {"name": "model_run_at", "type": "DateTime", "default_type": "", "comment": ""},
        ],
        "fct_orders_daily": [
            {"name": "order_id", "type": "String", "default_type": "", "comment": ""},
            {"name": "user_id", "type": "UInt64", "default_type": "", "comment": ""},
            {"name": "order_date", "type": "Date", "default_type": "", "comment": ""},
            {"name": "total_amount", "type": "Decimal(18, 2)", "default_type": "", "comment": ""},
            {"name": "currency", "type": "String", "default_type": "", "comment": ""},
            {"name": "status", "type": "String", "default_type": "", "comment": ""},
            {"name": "payment_method", "type": "String", "default_type": "", "comment": ""},
            {"name": "item_count", "type": "UInt32", "default_type": "", "comment": ""},
            {"name": "country", "type": "String", "default_type": "", "comment": ""},
        ],
    }
    return cols.get(tname, [])
_fake_ch.describe_table.side_effect = _fake_describe_table
_fake_ch.sample_rows.return_value = []
_fake_ch.get_table_stats.return_value = {"row_count": 0, "size_bytes": 0}
_fake_ch.client.command.return_value = "1"
_fake_ch.connect = MagicMock()

_fake_ss = MagicMock(spec=[
    "health", "list_dashboards", "get_dashboard", "get_chart", "get_dataset",
    "_base", "connect",
])
# 用 AsyncMock 支持 await
from unittest.mock import AsyncMock
_fake_ss.health = AsyncMock(return_value={"status": "ok", "mode": "mock", "url": "http://mock-superset"})
_fake_ss.list_dashboards = AsyncMock(return_value=[
    {
        "id": 1,
        "slug": "orders-risk-overview",
        "title": "Orders Risk Overview",
        "json_metadata": json.dumps({"chartId": [101]}),
        "slices": [],
    }
])
_fake_ss.get_dashboard = AsyncMock(side_effect=lambda did: {
    "id": did,
    "slug": "orders-risk-overview",
    "title": "Orders Risk Overview",
    "json_metadata": json.dumps({"chartId": [101]}),
})
_fake_ss.get_chart = AsyncMock(side_effect=lambda cid: {
    "id": cid,
    "slice_name": "Daily Risk Score",
    "datasource_id": 201,
    "datasource_type": "dataset",
})
_fake_ss.get_dataset = AsyncMock(side_effect=lambda did: {
    "id": did,
    "table_name": "fct_orders_risk_daily",
    "schema": "default",
    "database": {"database_name": "shop", "name": "shop"},
})
_fake_ss._base = "http://mock-superset"

patches = [
    patch("idm_api.skills.runner.get_clickhouse_mcp", return_value=_fake_ch),
    patch("idm_api.skills.mcp.get_clickhouse_mcp", return_value=_fake_ch),
    patch("idm_api.skills.mcp.get_superset_mcp", return_value=_fake_ss),
    patch("idm_api.skills.builtin.parse_superset_dashboard.get_superset_mcp", return_value=_fake_ss),
]
for p in patches:
    p.start()


async def _setup_db():
    engine = create_async_engine(
        SQLITE_URL,
        echo=False,
        connect_args={"check_same_thread": False},
    )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _register_uuid(dbapi_conn, _):  # noqa: ANN001
        try:
            dbapi_conn.create_function("gen_random_uuid", 0, lambda: str(uuid.uuid4()))
        except Exception:
            pass

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    return engine, factory


def banner(msg: str) -> None:
    print(f"\n=== {msg} ===")


async def _run(stage: str, skill: str, inputs: dict[str, Any], factory) -> tuple[bool, str]:
    from idm_api.skills.runner import run_skill
    try:
        async with factory() as db:
            r = await run_skill(skill, inputs=inputs, db=db)
        if r.ok:
            return True, f"items={len(r.output.items)}, summary={json.dumps(r.output.summary, default=str)[:200]}"
        return False, f"err={r.error}"
    except Exception as e:  # noqa: BLE001
        return False, f"exc={type(e).__name__}: {e}"


async def main() -> int:
    print(f"Fixtures: GCS={GCS_ROOT}  GitHub={GH_ROOT}")
    engine, factory = await _setup_db()
    results: list[tuple[str, bool, str]] = []

    stages: list[tuple[str, str, str, dict[str, Any]]] = [
        (
            "stage 1: GCS raw",
            "discover_gcs_assets",
            "GCS",
            {"bucket": "company-raw", "prefix": "orders/2026/06",
             "stage": 1, "source_role": "raw", "apply": True},
        ),
        (
            "stage 1: Airflow DAG",
            "parse_airflow_dag",
            "DAG",
            {"dag_file_path": str(FIXTURE_DIR / "github" / "company" / "dwh" / "dags" / "etl_orders_daily.py"),
             "stage": 1, "apply": True},
        ),
        (
            "stage 1: Flink preprocess",
            "parse_flink_job",
            "SQL",
            {"repo": "company/dwh", "paths": ["flink_jobs/orders_preprocess.sql"],
             "stage": 1, "transform_subtype": "preprocess", "apply": True},
        ),
        (
            "stage 2: GCS model-input",
            "discover_gcs_assets",
            "GCS",
            {"bucket": "company-model-input", "prefix": "orders/2026/06",
             "stage": 2, "source_role": "model_input", "apply": True},
        ),
        (
            "stage 3: MEX io.yaml",
            "parse_mex_io",
            "YAML",
            {"repo": "company/mex-models", "paths": ["orders/io.yaml"],
             "pipeline_stage": 3, "apply": True},
        ),
        (
            "stage 4: GCS model-output",
            "discover_gcs_assets",
            "GCS",
            {"bucket": "company-model-output", "prefix": "orders/2026/06",
             "stage": 4, "source_role": "model_output", "apply": True},
        ),
        (
            "stage 5: Flink load",
            "parse_flink_job",
            "SQL",
            {"repo": "company/dwh", "paths": ["flink_jobs/load_orders_risk_to_clickhouse.sql"],
             "stage": 5, "transform_subtype": "load_ch", "apply": True},
        ),
        (
            "stage 5: ClickHouse",
            "discover_clickhouse_assets",
            "CH",
            {"database": "shop",
             "include_tables": ["fct_orders_risk_daily", "fct_orders_daily"]},
        ),
        (
            "stage 6: Superset export (dry_run)",
            "parse_superset_dashboard",
            "SS",
            {"stage": 6, "service_name": "superset-demo",
             "include_charts": True, "include_datasets": True, "dry_run": True},
        ),
    ]

    for name, skill, _, inputs in stages:
        banner(name)
        ok, msg = await _run(name, skill, inputs, factory)
        flag = "OK  " if ok else "FAIL"
        print(f"  [{flag}] {msg}")
        results.append((name, ok, msg))

    # === 总结 ===
    banner("SUMMARY")
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    for name, ok, _ in results:
        flag = "[OK]  " if ok else "[FAIL]"
        print(f"  {flag} {name}")
    print(f"\n{passed}/{total} stages passed")

    # === 资源清理 ===
    await engine.dispose()
    try:
        os.unlink(SQLITE_PATH)
    except FileNotFoundError:
        pass

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
