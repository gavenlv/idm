"""/health 路由测试."""
import pytest

pytestmark = pytest.mark.asyncio


async def test_liveness_ok(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"


async def test_readiness(client):
    r = await client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in {"ok", "degraded", "down"}
    assert "checks" in body
    assert body["env"] == "local"


async def test_info(client):
    r = await client.get("/health/info")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "idm-api"
    assert body["planner_model"] == "gpt-5"
    assert body["default_model"] == "gpt-5"
