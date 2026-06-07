"""用户反馈 API: 对接 Eval Harness 的 FeedbackStore.

端点:
- POST /api/v1/feedback  : 用户提交 👍/👎 (+ 修正 payload)
- GET  /api/v1/feedback  : 列反馈 (skill 过滤)
- POST /api/v1/feedback/few-shots/{skill} : 导出 few-shot JSONL 到服务端磁盘
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from idm_api.eval.feedback import (
    FeedbackRecord,
    build_few_shots,
    get_feedback_store,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])


class FeedbackIn(BaseModel):
    skill: str = Field(..., description="Skill 名, e.g. infer_table_description")
    case_key: str = Field(..., description="业务键: fqn / suggestion_id / question")
    pred: dict[str, Any] = Field(default_factory=dict, description="LLM 原始输出")
    accepted: bool
    reason: str | None = None
    new_payload: dict[str, Any] | None = Field(
        default=None, description="用户修正后的预期输出 (拒绝时填写)"
    )
    user_email: str | None = None


class FeedbackOut(BaseModel):
    id: str
    skill: str
    case_key: str
    accepted: bool
    created_at: str


class FewShotBuildOut(BaseModel):
    skill: str
    count: int
    out_path: str


def _to_out(r: FeedbackRecord) -> FeedbackOut:
    return FeedbackOut(
        id=r.id,
        skill=r.skill,
        case_key=r.case_key,
        accepted=r.accepted,
        created_at=r.created_at,
    )


@router.post("", response_model=FeedbackOut, status_code=201)
async def submit_feedback(payload: FeedbackIn) -> FeedbackOut:
    """用户对 Skill 输出提交反馈."""
    store = get_feedback_store()
    rec = store.add_simple(
        skill=payload.skill,
        case_key=payload.case_key,
        pred=payload.pred,
        accepted=payload.accepted,
        reason=payload.reason,
        new_payload=payload.new_payload,
        user_email=payload.user_email,
    )
    logger.info(
        "feedback submitted: skill=%s accepted=%s key=%s user=%s",
        rec.skill,
        rec.accepted,
        rec.case_key,
        payload.user_email,
    )
    return _to_out(rec)


@router.get("", response_model=list[FeedbackOut])
async def list_feedback(
    skill: str | None = Query(None),
    accepted: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> list[FeedbackOut]:
    store = get_feedback_store()
    items = store.list(skill=skill, accepted=accepted)
    return [_to_out(r) for r in items[:limit]]


@router.post("/few-shots/{skill}", response_model=FewShotBuildOut)
async def build_few_shot_file(
    skill: str,
    out_path: str = Query(
        "few_shots/{skill}.jsonl",
        description="相对路径; 占位符 {skill} 会被替换",
    ),
    k: int = Query(5, ge=1, le=20),
) -> FewShotBuildOut:
    """从 FeedbackStore 导出该 skill 的 few-shot JSONL.

    默认写到 ./few_shots/<skill>.jsonl (相对 CWD).
    """
    store = get_feedback_store()
    resolved = out_path.format(skill=skill)
    p = build_few_shots(store, skill, resolved, k=k)
    examples = store.export_few_shots(skill, k=k)
    return FewShotBuildOut(skill=skill, count=len(examples), out_path=str(p))


@router.get("/few-shots/{skill}/preview")
async def preview_few_shots(skill: str, k: int = Query(5, ge=1, le=20)) -> list[dict]:
    """预览该 skill 的 few-shot (不落盘)."""
    store = get_feedback_store()
    examples = store.export_few_shots(skill, k=k)
    return [ex.model_dump(mode="json") for ex in examples]
