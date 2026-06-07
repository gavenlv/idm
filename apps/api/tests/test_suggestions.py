"""/api/v1/suggestions 路由测试 (AI 审核流)."""
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _seed_suggestion(client: AsyncClient) -> str:
    """直接通过 service name 创建, 然后插入 ai_suggestion (M1 简化: 用 SQL)."""
    # 没有公开的 ai_suggestion POST, 测试时直接走 DB
    from sqlalchemy import insert
    from idm_api import db as db_module
    from idm_kg.models.ai_suggestion import AISuggestion

    factory = db_module.get_session_factory()
    async with factory() as session:
        row = await session.execute(
            insert(AISuggestion).values(
                suggestion_type="description",
                target_type="table",
                target_id="00000000-0000-0000-0000-000000000001",
                payload={"description": "这是一张订单表"},
                confidence=0.92,
                model="gpt-5",
                skill="infer_table_description",
                use_case_id="test",
            ).returning(AISuggestion.id)
        )
        sid = row.scalar_one()
        await session.commit()
    return str(sid)


async def test_list_suggestions_default_pending(client):
    sid = await _seed_suggestion(client)
    r = await client.get("/api/v1/suggestions")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == sid
    assert body["items"][0]["status"] == "pending"


async def test_approve_suggestion(client):
    sid = await _seed_suggestion(client)
    r = await client.post(f"/api/v1/suggestions/{sid}/approve", json={"review_note": "LGTM"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "approved"
    assert body["reviewed_by"] == "system:tester"
    assert body["review_note"] == "LGTM"


async def test_approve_already_approved_409(client):
    sid = await _seed_suggestion(client)
    r1 = await client.post(f"/api/v1/suggestions/{sid}/approve")
    assert r1.status_code == 200
    r2 = await client.post(f"/api/v1/suggestions/{sid}/approve")
    assert r2.status_code == 409


async def test_reject_suggestion(client):
    sid = await _seed_suggestion(client)
    r = await client.post(f"/api/v1/suggestions/{sid}/reject", json={"review_note": "不准"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "rejected"


async def test_approve_404(client):
    r = await client.post("/api/v1/suggestions/00000000-0000-0000-0000-000000000000/approve")
    assert r.status_code == 404
