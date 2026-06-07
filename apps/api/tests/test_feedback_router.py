"""Feedback API router 测试."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_submit_feedback(client: AsyncClient):
    r = await client.post(
        "/api/v1/feedback",
        json={
            "skill": "nl2sql",
            "case_key": "q-1",
            "pred": {"sql": "SELECT 1 FROM t LIMIT 5"},
            "accepted": True,
            "user_email": "alice@example.com",
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["skill"] == "nl2sql"
    assert data["accepted"] is True
    assert data["case_key"] == "q-1"
    assert data["id"]


@pytest.mark.asyncio
async def test_list_feedback_filter(client: AsyncClient):
    # 提 2 条
    await client.post(
        "/api/v1/feedback",
        json={
            "skill": "nl2sql",
            "case_key": "q-1",
            "pred": {"sql": "x"},
            "accepted": True,
        },
    )
    await client.post(
        "/api/v1/feedback",
        json={
            "skill": "infer_table_description",
            "case_key": "t-1",
            "pred": {"description": "y"},
            "accepted": False,
        },
    )
    r = await client.get("/api/v1/feedback?skill=nl2sql")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["skill"] == "nl2sql"

    r = await client.get("/api/v1/feedback?accepted=false")
    assert len(r.json()) == 1
    assert r.json()[0]["accepted"] is False


@pytest.mark.asyncio
async def test_build_few_shots(client: AsyncClient, tmp_path):
    # 先给 2 条 rejected + 1 条 accepted
    for _ in range(2):
        await client.post(
            "/api/v1/feedback",
            json={
                "skill": "nl2sql",
                "case_key": "q-bad",
                "pred": {"sql": "DROP TABLE t"},
                "accepted": False,
                "new_payload": {"sql": "SELECT 1 FROM t LIMIT 1"},
                "reason": "禁止 DML",
            },
        )
    await client.post(
        "/api/v1/feedback",
        json={
            "skill": "nl2sql",
            "case_key": "q-good",
            "pred": {"sql": "SELECT * FROM t LIMIT 5"},
            "accepted": True,
        },
    )

    out_file = tmp_path / "fs.jsonl"
    r = await client.post(
        f"/api/v1/feedback/few-shots/nl2sql?out_path={out_file}&k=5"
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["skill"] == "nl2sql"
    assert data["count"] == 2  # 1 正例 + 1 反例
    assert str(out_file) == data["out_path"]
    assert out_file.exists()
    lines = out_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


@pytest.mark.asyncio
async def test_preview_few_shots(client: AsyncClient):
    await client.post(
        "/api/v1/feedback",
        json={
            "skill": "s",
            "case_key": "k",
            "pred": {"v": 1},
            "accepted": True,
        },
    )
    r = await client.get("/api/v1/feedback/few-shots/s/preview?k=3")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["skill"] == "s"
    assert items[0]["weight"] == 100
