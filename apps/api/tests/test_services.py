"""/api/v1/services 路由测试."""
import pytest

pytestmark = pytest.mark.asyncio


async def test_list_services_empty(client):
    r = await client.get("/api/v1/services")
    assert r.status_code == 200
    assert r.json() == []


async def test_create_service_and_list(client):
    payload = {
        "name": "clickhouse-prod",
        "type": "clickhouse",
        "description": "生产 CH 集群",
        "tier": "critical",
    }
    r = await client.post("/api/v1/services", json=payload)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["name"] == "clickhouse-prod"
    assert created["tier"] == "critical"
    assert "id" in created

    r = await client.get("/api/v1/services")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["name"] == "clickhouse-prod"


async def test_create_service_duplicate_409(client):
    payload = {"name": "ch", "type": "clickhouse"}
    r = await client.post("/api/v1/services", json=payload)
    assert r.status_code == 201
    r = await client.post("/api/v1/services", json=payload)
    assert r.status_code == 409


async def test_get_service_404(client):
    r = await client.get("/api/v1/services/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404
