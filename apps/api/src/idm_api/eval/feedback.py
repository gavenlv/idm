"""用户反馈层: 接受/拒绝 + Few-shot 自动维护.

设计: docs/design/eval-harness.md §7, §10

数据流:
  [Skill 跑完]   ->  [pred 落到 ai_suggestion]
  [用户点 👍/👎] ->  [FeedbackStore.add()]   ->  [llm_feedback 表 / 内存]
  [定时任务]     ->  [build_few_shots(skill)] ->  [JSONL 喂给 LLM]
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class FeedbackRecord(BaseModel):
    """1 条用户对 LLM 输出的反馈."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    skill: str
    case_key: str  # fqn / question / suggestion_id 等
    pred: dict[str, Any] = Field(default_factory=dict)
    new_payload: dict[str, Any] | None = None  # 用户修正后
    accepted: bool
    reason: str | None = None
    user_email: str | None = None
    created_at: str = Field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class FewShotExample(BaseModel):
    """1 条 few-shot: question/case_key + input + expected output."""

    skill: str
    case_key: str
    input: dict[str, Any] = Field(default_factory=dict)
    expected: dict[str, Any] = Field(default_factory=dict)
    weight: int = 50  # 优先级, 默认 50
    source: str = "feedback"  # feedback / gold / expert
    note: str | None = None

    def render_chat(self) -> list[dict[str, str]]:
        """渲染为 OpenAI / LiteLLM chat 格式 (单条 user/assistant 轮)."""
        return [
            {
                "role": "user",
                "content": json.dumps(self.input, ensure_ascii=False),
            },
            {
                "role": "assistant",
                "content": json.dumps(self.expected, ensure_ascii=False),
            },
        ]


class FeedbackStore:
    """用户反馈存储.

    实现策略:
    - 默认走内存 (list), 方便测试 / 单进程
    - 可选: 提供 add_async() 走 Postgres (后续在 routers/feedback.py 接入)
    - 提供 export_few_shots() 输出 JSONL
    """

    def __init__(self) -> None:
        self._records: list[FeedbackRecord] = []

    def add(self, rec: FeedbackRecord) -> None:
        self._records.append(rec)
        logger.info(
            "feedback recorded: skill=%s accepted=%s key=%s",
            rec.skill,
            rec.accepted,
            rec.case_key,
        )

    def add_simple(
        self,
        *,
        skill: str,
        case_key: str,
        pred: dict[str, Any],
        accepted: bool,
        reason: str | None = None,
        new_payload: dict[str, Any] | None = None,
        user_email: str | None = None,
    ) -> FeedbackRecord:
        rec = FeedbackRecord(
            skill=skill,
            case_key=case_key,
            pred=pred,
            new_payload=new_payload,
            accepted=accepted,
            reason=reason,
            user_email=user_email,
        )
        self.add(rec)
        return rec

    def list(
        self,
        skill: str | None = None,
        accepted: bool | None = None,
    ) -> list[FeedbackRecord]:
        out = self._records
        if skill is not None:
            out = [r for r in out if r.skill == skill]
        if accepted is not None:
            out = [r for r in out if r.accepted == accepted]
        return out

    def clear(self) -> None:
        self._records.clear()

    # === 聚合: 反例 (拒绝) 触发 few-shot 强化 ===
    def rejected_for(self, skill: str, min_count: int = 2) -> list[FeedbackRecord]:
        """同一个 case_key 拒绝 >= min_count 次, 才进反例 few-shot."""
        cnt: dict[str, list[FeedbackRecord]] = defaultdict(list)
        for r in self.list(skill=skill, accepted=False):
            cnt[r.case_key].append(r)
        out: list[FeedbackRecord] = []
        for items in cnt.values():
            if len(items) >= min_count:
                # 取最新 1 条
                out.append(items[-1])
        return out

    def accepted_for(self, skill: str, min_count: int = 1) -> list[FeedbackRecord]:
        """采纳 >= min_count 次的 case, 进正例 few-shot."""
        cnt: dict[str, list[FeedbackRecord]] = defaultdict(list)
        for r in self.list(skill=skill, accepted=True):
            cnt[r.case_key].append(r)
        return [items[-1] for items in cnt.values() if len(items) >= min_count]

    # === 导出 few-shot ===
    def export_few_shots(
        self, skill: str, *, k: int = 5, min_reject_count: int = 2
    ) -> list[FewShotExample]:
        """对 1 个 skill 产 few-shot.

        策略:
        - 正例: 用户采纳的 (accepted=True), weight=100
        - 反例: 同一 key 被拒绝多次, 用 new_payload 当 expected, weight=70
        - 没有 new_payload 的反例丢弃
        - 截断到 k 条
        """
        accepted = self.accepted_for(skill, min_count=1)
        rejected = self.rejected_for(skill, min_count=min_reject_count)

        examples: list[FewShotExample] = []
        for r in accepted:
            # accepted 情况下, expected = pred (LLM 输出被采纳)
            # 如有 new_payload 则用修正版
            expected = r.new_payload or r.pred
            examples.append(
                FewShotExample(
                    skill=skill,
                    case_key=r.case_key,
                    input={"case_key": r.case_key, "raw": r.pred.get("input", {})},
                    expected=expected,
                    weight=100,
                    source="feedback_accept",
                )
            )
        for r in rejected:
            if not r.new_payload:
                # 用户没给修正, 跳过
                continue
            examples.append(
                FewShotExample(
                    skill=skill,
                    case_key=r.case_key,
                    input={"case_key": r.case_key, "raw": r.pred.get("input", {})},
                    expected=r.new_payload,
                    weight=70,
                    source="feedback_reject_corrected",
                    note=r.reason,
                )
            )

        # 简单按 weight desc 截断
        examples.sort(key=lambda e: -e.weight)
        return examples[:k]


# === 工具: 全局 store ===
_GLOBAL_STORE: FeedbackStore | None = None


def get_feedback_store() -> FeedbackStore:
    global _GLOBAL_STORE
    if _GLOBAL_STORE is None:
        _GLOBAL_STORE = FeedbackStore()
    return _GLOBAL_STORE


def reset_feedback_store() -> None:
    """测试用."""
    global _GLOBAL_STORE
    _GLOBAL_STORE = None


# === 工具: 把 feedback 转成 few-shot JSONL 落到磁盘 ===
def build_few_shots(
    store: FeedbackStore,
    skill: str,
    out_path: str | Path,
    *,
    k: int = 5,
) -> Path:
    """导出 few-shot JSONL 到本地. 供 SkillRunner 在拼 prompt 时读取."""
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    examples = store.export_few_shots(skill, k=k)
    with p.open("w", encoding="utf-8") as f:
        for ex in examples:
            f.write(ex.model_dump_json() + "\n")
    logger.info("wrote %d few-shot examples for %s -> %s", len(examples), skill, p)
    return p
