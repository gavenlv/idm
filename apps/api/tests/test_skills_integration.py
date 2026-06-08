"""Skills 集成测试: 验证所有 builtin skill 都能注册 + 接受输入 + 跑通 (mock LLM/MCP)."""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


def U() -> uuid.UUID:
    return uuid.uuid4()


# === Mock LLM ===
class FakeLLMResp:
    def __init__(self, content: str) -> None:
        self._d = {
            "content": content,
            "model": "mock-gpt-5",
            "tier": "primary",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "prompt_hash": "deadbeef",
        }

    def get(self, k, default=None):
        return self._d.get(k, default)


class FakeLLM:
    """Fake LLMRouter, 根据 prompt 关键词返回不同 SQL/JSON."""

    async def complete(self, messages, **kwargs):  # noqa: ANN001
        user = next((m["content"] for m in messages if m.get("role") == "user"), "")
        user_lower = user.lower()
        # NL2SQL: 关键词 "schema" + "sql" 出现
        if "schema" in user_lower and ("sql" in user_lower or "select" in user_lower) and "json" in user_lower:
            return FakeLLMResp('{"sql": "SELECT 1 AS a LIMIT 3", "explanation": "mock"}')
        if "description" in user_lower and ("table" in user_lower or "fqn" in user_lower):
            return FakeLLMResp("这是一张 mock 生成的测试表, 包含示例数据。")
        if "pii" in user_lower and "classify" in user_lower:
            return FakeLLMResp('{"columns": [{"name": "email", "pii_class": "email", "confidence": 0.95}]}')
        if "owner" in user_lower or "团队" in user:
            return FakeLLMResp('{"user_email": "data-eng@example.com", "user_name": "Data Eng", "team": "data-eng", "role": "owner", "confidence": 0.7, "reasoning": "mock"}')
        return FakeLLMResp("{}")


# === Mock ClickHouse MCP ===
class FakeCH:
    def list_tables(self, database: str | None = None):
        return ["mock_t1", "mock_t2"]

    def get_table_stats(self, database: str, table: str):
        return {"parts": 5, "row_count": 1000, "size_bytes": 1024, "last_modified": "2026-01-01"}

    def run_query(self, sql: str):
        return [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]


@pytest.fixture
def fake_mcp():
    return FakeCH()


@pytest.fixture
def fake_llm():
    return FakeLLM()


# === 1) skill 注册列表 ===
async def test_all_builtin_skills_registered(client):
    r = await client.get("/api/v1/skills")
    assert r.status_code == 200
    items = r.json().get("items", [])
    names = {s["name"] for s in items}
    # M1 必须注册的 8 个 skill
    expected = {
        "discover_clickhouse_assets",
        "infer_table_description",
        "classify_pii_columns",
        "parse_dbt_manifest",
        "analyze_dbt_code",
        "parse_superset_dashboard",
        "infer_table_owners",
        "nl2sql",
    }
    missing = expected - names
    assert not missing, f"missing skills: {missing}"


# === 2) NL2SQL skill: 5 层 guard 在 mock LLM 下应该全部通过 ===
async def test_nl2sql_skill_runs(app_with_db, fake_llm, fake_mcp):
    from idm_kg.models.database import Database
    from idm_kg.models.schema import Schema
    from idm_kg.models.service import Service
    from idm_kg.models.table_asset import TableAsset
    from sqlalchemy import select

    # 直接用 db 写入测试表
    from idm_api.db import _session_factory
    async with _session_factory() as db:
        svc = Service(id=U(), name="mock-svc", type="clickhouse", description="Mock")
        db.add(svc)
        await db.flush()
        db_obj = Database(id=U(), service_id=svc.id, name="mock-svc", description="Mock DB")
        db.add(db_obj)
        await db.flush()
        sch = Schema(id=U(), database_id=db_obj.id, name="default")
        db.add(sch)
        await db.flush()
        t = TableAsset(
            id=U(), fqn="mock-svc.default.mock_t1", name="mock_t1",
            schema_id=sch.id, asset_type="table", tier="normal", status="active",
        )
        db.add(t)
        await db.commit()

    with patch("idm_api.skills.runner.get_clickhouse_mcp", return_value=fake_mcp), \
         patch("idm_api.skills.runner.get_llm_router", return_value=fake_llm), \
         patch("idm_api.skills.builtin.nl2sql.get_clickhouse_mcp", return_value=fake_mcp):
        transport = ASGITransport(app=app_with_db)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/v1/skills/run", json={
                "name": "nl2sql",
                "inputs": {
                    "question": "查 mock_t1 的 3 行",
                    "service": "mock-svc",
                    "max_rows": 3,
                    "dry_run": True,
                },
            })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, body.get("error")
    items = body["output"]["items"]
    assert len(items) == 1
    v = items[0]["validation"]
    assert "schema" in v["passed_guards"]
    assert "sql_safety" in v["passed_guards"]
    assert "row_limit" in v["passed_guards"]
    assert "pii" in v["passed_guards"]
    sql = items[0]["sql"]
    assert "SELECT" in sql
    assert "LIMIT" in sql


# === 3) NL2SQL safety guard: DELETE 拒 ===
async def test_nl2sql_blocks_dml():
    from idm_api.skills.builtin.nl2sql import _guard_sql_safety, _guard_row_limit
    ok, msg = _guard_sql_safety("DELETE FROM foo")
    assert not ok and "DML" in msg
    ok, msg = _guard_sql_safety("SELECT 1; SELECT 2")
    assert not ok and "multiple" in msg
    # comment 中 DML keyword 不触发
    ok, msg = _guard_sql_safety("SELECT 1  -- comment with delete keyword")
    assert ok, msg
    # 行限制
    sql, _, note = _guard_row_limit("SELECT 1", 50)
    assert "LIMIT 50" in sql and "injected" in note


# === 4) infer_table_owners skill: dry-run 不写 KG ===
async def test_infer_table_owners_skill(app_with_db, fake_llm, fake_mcp):
    from idm_kg.models.database import Database
    from idm_kg.models.schema import Schema
    from idm_kg.models.service import Service
    from idm_kg.models.table_asset import TableAsset

    from idm_api.db import _session_factory
    async with _session_factory() as db:
        svc = Service(id=U(), name="owner-test", type="clickhouse", description="OT")
        db.add(svc); await db.flush()
        db_obj = Database(id=U(), service_id=svc.id, name="owner-test", description="OT DB")
        db.add(db_obj); await db.flush()
        sch = Schema(id=U(), database_id=db_obj.id, name="default")
        db.add(sch); await db.flush()
        for i, n in enumerate(["fct_orders", "dim_users"]):
            t = TableAsset(
                id=U(), fqn=f"owner-test.default.{n}", name=n,
                schema_id=sch.id, asset_type="table", tier="normal", status="active",
            )
            db.add(t)
        await db.commit()

    with patch("idm_api.skills.runner.get_clickhouse_mcp", return_value=fake_mcp), \
         patch("idm_api.skills.runner.get_llm_router", return_value=fake_llm):
        transport = ASGITransport(app=app_with_db)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/v1/skills/run", json={
                "name": "infer_table_owners",
                "inputs": {
                    "service": "owner-test",
                    "apply": False,
                    "llm_threshold": 0.5,
                },
            })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    summary = body["output"]["summary"]
    assert summary["tables_scanned"] == 2


# === 5) detect_anomalies skill ===
async def test_detect_anomalies_skill(app_with_db, fake_llm, fake_mcp):
    from idm_kg.models.database import Database
    from idm_kg.models.schema import Schema
    from idm_kg.models.service import Service
    from idm_kg.models.table_asset import TableAsset

    from idm_api.db import _session_factory
    async with _session_factory() as db:
        svc = Service(id=U(), name="anom-test", type="clickhouse", description="AT")
        db.add(svc); await db.flush()
        db_obj = Database(id=U(), service_id=svc.id, name="anom-test", description="AT DB")
        db.add(db_obj); await db.flush()
        sch = Schema(id=U(), database_id=db_obj.id, name="default")
        db.add(sch); await db.flush()
        for i, n in enumerate(["t1", "t2"]):
            t = TableAsset(
                id=U(), fqn=f"anom-test.default.{n}", name=n,
                schema_id=sch.id, asset_type="table", tier="normal", status="active",
            )
            db.add(t)
        await db.commit()

    with patch("idm_api.skills.runner.get_clickhouse_mcp", return_value=fake_mcp), \
         patch("idm_api.skills.runner.get_llm_router", return_value=fake_llm), \
         patch("idm_api.skills.builtin.detect_anomalies.get_clickhouse_mcp", return_value=fake_mcp):
        transport = ASGITransport(app=app_with_db)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/v1/skills/run", json={
                "name": "detect_anomalies",
                "inputs": {
                    "service": "anom-test",
                    "apply": False,
                    "skip_drift": True,
                    "skip_null": True,
                },
            })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    summary = body["output"]["summary"]
    assert summary["tables_scanned"] == 2
    # 应至少报 1 个 owner_gap
    findings = body["output"]["items"]
    assert any(f["kind"] == "owner_gap" for f in findings), f"no owner_gap in {findings}"
