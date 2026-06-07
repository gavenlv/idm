"""用户反馈 + Few-shot 自动维护 测试."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from idm_api.eval.feedback import (
    FeedbackRecord,
    FeedbackStore,
    FewShotExample,
    build_few_shots,
    get_feedback_store,
    reset_feedback_store,
)


@pytest.fixture
def store() -> FeedbackStore:
    reset_feedback_store()
    return get_feedback_store()


# === 1) FeedbackRecord 序列化 ===
def test_feedback_record_defaults():
    r = FeedbackRecord(skill="nl2sql", case_key="q-1", accepted=True)
    assert r.id  # auto uuid
    assert r.created_at.endswith("Z")
    assert r.pred == {}
    assert r.new_payload is None


def test_feedback_record_to_dict():
    r = FeedbackRecord(
        skill="nl2sql",
        case_key="q-1",
        pred={"sql": "SELECT 1"},
        accepted=True,
    )
    d = r.to_dict()
    assert d["skill"] == "nl2sql"
    assert d["accepted"] is True
    assert d["pred"]["sql"] == "SELECT 1"


# === 2) FeedbackStore add / list ===
def test_store_add_and_list(store: FeedbackStore):
    rec = store.add_simple(
        skill="nl2sql",
        case_key="q-1",
        pred={"sql": "SELECT * FROM t"},
        accepted=True,
        user_email="alice@example.com",
    )
    assert rec.id
    items = store.list()
    assert len(items) == 1
    assert items[0].skill == "nl2sql"


def test_store_filter_by_skill(store: FeedbackStore):
    store.add_simple(skill="a", case_key="k1", pred={}, accepted=True)
    store.add_simple(skill="b", case_key="k2", pred={}, accepted=False)
    assert len(store.list(skill="a")) == 1
    assert len(store.list(skill="b")) == 1
    assert len(store.list(accepted=False)) == 1


def test_store_rejected_for_min_count(store: FeedbackStore):
    # 同一 key 被拒绝 3 次 -> 触发反例
    for _ in range(3):
        store.add_simple(
            skill="nl2sql",
            case_key="q-bad",
            pred={"sql": "DROP TABLE t"},
            accepted=False,
            new_payload={"sql": "SELECT 1 FROM t LIMIT 1"},
        )
    # 另一 key 只被拒绝 1 次 -> 不触发
    store.add_simple(
        skill="nl2sql",
        case_key="q-once",
        pred={"sql": "BAD"},
        accepted=False,
    )
    rejected = store.rejected_for("nl2sql", min_count=2)
    assert len(rejected) == 1
    assert rejected[0].case_key == "q-bad"


def test_store_accepted_for(store: FeedbackStore):
    store.add_simple(skill="s", case_key="k1", pred={"v": 1}, accepted=True)
    store.add_simple(skill="s", case_key="k1", pred={"v": 1}, accepted=True)
    store.add_simple(skill="s", case_key="k2", pred={"v": 2}, accepted=True)
    accepted = store.accepted_for("s")
    assert {r.case_key for r in accepted} == {"k1", "k2"}


# === 3) export_few_shots ===
def test_export_few_shots_positive_and_negative(store: FeedbackStore):
    # 正例: 1 个被采纳
    store.add_simple(
        skill="nl2sql",
        case_key="q-good",
        pred={"sql": "SELECT 1 FROM t LIMIT 5"},
        accepted=True,
    )
    # 反例: 1 个被多次拒绝并有修正
    for _ in range(2):
        store.add_simple(
            skill="nl2sql",
            case_key="q-bad",
            pred={"sql": "DROP TABLE t"},
            accepted=False,
            new_payload={"sql": "SELECT 1 FROM t LIMIT 1"},
            reason="禁止 DML",
        )
    examples = store.export_few_shots("nl2sql", k=5, min_reject_count=2)
    # 应该有 2 条
    assert len(examples) == 2
    # weight desc: accepted (100) 在前
    assert examples[0].weight == 100
    assert examples[1].weight == 70
    assert examples[1].source == "feedback_reject_corrected"


def test_export_few_shots_drops_unfixed_reject(store: FeedbackStore):
    store.add_simple(
        skill="nl2sql",
        case_key="q-bad",
        pred={"sql": "BAD"},
        accepted=False,
        reason="错了",
    )
    examples = store.export_few_shots("nl2sql", k=5, min_reject_count=2)
    # 没有 new_payload 的反例被丢弃
    assert examples == []


def test_export_few_shots_truncate_k(store: FeedbackStore):
    for i in range(10):
        store.add_simple(
            skill="s",
            case_key=f"k{i}",
            pred={"v": i},
            accepted=True,
        )
    examples = store.export_few_shots("s", k=3)
    assert len(examples) == 3


# === 4) build_few_shots 写盘 ===
def test_build_few_shots_writes_jsonl(store: FeedbackStore, tmp_path: Path):
    store.add_simple(
        skill="nl2sql",
        case_key="q1",
        pred={"sql": "SELECT 1"},
        accepted=True,
    )
    out = build_few_shots(store, "nl2sql", tmp_path / "few_shots" / "nl2sql.jsonl", k=5)
    assert out.exists()
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["skill"] == "nl2sql"
    assert obj["weight"] == 100


# === 5) FewShotExample 渲染 ===
def test_few_shot_render_chat():
    ex = FewShotExample(
        skill="nl2sql",
        case_key="q1",
        input={"question": "查 x"},
        expected={"sql": "SELECT x"},
    )
    chat = ex.render_chat()
    assert len(chat) == 2
    assert chat[0]["role"] == "user"
    assert chat[1]["role"] == "assistant"
    assert "查 x" in chat[0]["content"]


# === 6) reset_feedback_store ===
def test_reset_clears_global():
    """全局 store 在 reset 后被清空, 下次 get 是新实例."""
    reset_feedback_store()
    s1 = get_feedback_store()
    s1.add_simple(skill="s", case_key="k", pred={}, accepted=True)
    assert len(s1.list()) == 1
    reset_feedback_store()
    s2 = get_feedback_store()
    assert s2 is not s1
    assert len(s2.list()) == 0
    # 清理
    reset_feedback_store()
