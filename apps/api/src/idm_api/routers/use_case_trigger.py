"""/api/v1/use-cases/{id}/trigger + /rescan + /stages/{n}/trigger

功能:
  - 触发 use case 编排 (analyze_data_pipeline)
  - 单阶段触发 (按 stage 编号过滤)
  - 重新扫描 (rescan = trigger 的幂等别名)

设计原则 (源自 AGENT_INSTRUCTIONS.md §16.5):
  - 幂等: 资产/血缘 全部走 upsert
  - 超时控制: skill_runner 内部 30s/单步, 总时长由 client 控制
  - 不阻塞: client 应使用 streaming / long-polling

这些端点是"系统触发"的功能入口, 不替代主动 Skill 调用,
但提供:
  - 业务人员"按 use case" 跑 (业务视角)
  - 平台/CI 自动化"按 stage" 跑 (运维视角)
  - UI "rescan" 按钮 (运营视角)
"""
from __future__ import annotations

import logging
import time
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.db import get_db
from idm_api.routers.use_cases import _parse, _uc_dir
from idm_api.schemas import (
    UseCaseStageRequest,
    UseCaseTriggerRequest,
    UseCaseTriggerResponse,
)
from idm_api.skills.runner import run_skill

logger = logging.getLogger(__name__)

router = APIRouter()


def _load_use_case_spec(uc_id: str) -> dict[str, Any]:
    """读 use case YAML → spec (dict). 找不到 404."""
    base = _uc_dir(None)  # type: ignore[arg-type]
    path = base / f"{uc_id}.yml"
    if not path.exists():
        for f in base.glob("*.yml"):
            try:
                s = _parse(f.read_text(encoding="utf-8"), f)
            except HTTPException:
                continue
            if s.id == uc_id:
                path = f
                break
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"use case '{uc_id}' not found in {base}",
            )
    raw = path.read_text(encoding="utf-8")
    try:
        spec = yaml.safe_load(raw) or {}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid YAML in {path.name}: {e}",
        ) from e
    if str(spec.get("id") or uc_id) != uc_id:
        spec["id"] = uc_id
    return spec


async def _trigger_uc(
    uc_id: str,
    req: UseCaseTriggerRequest,
    db: AsyncSession,
) -> UseCaseTriggerResponse:
    """核心编排: 加载 → 过滤 → 调 analyze_data_pipeline."""
    started = time.perf_counter()
    use_case = _load_use_case_spec(uc_id)
    sources: list[dict[str, Any]] = list(use_case.get("sources") or [])
    if req.stages:
        allowed = {int(s) for s in req.stages}
        sources = [s for s in sources if s.get("stage") is not None and int(s.get("stage")) in allowed]
        use_case = {**use_case, "sources": sources}
    if not sources:
        return UseCaseTriggerResponse(
            ok=False,
            use_case_id=uc_id,
            output={"reason": "no sources matched filter"},
            error="no sources matched (check stages filter and use_case.sources)",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    inputs: dict[str, Any] = {"use_case": use_case, "apply": req.apply}
    if req.dry_run:
        inputs["dry_run"] = True
    result = await run_skill(
        "analyze_data_pipeline",
        inputs=inputs,
        use_case_id=uc_id,
        dry_run=req.dry_run,
        db=db,
    )
    return UseCaseTriggerResponse(
        ok=result.ok,
        use_case_id=uc_id,
        output=result.output.model_dump(),
        error=result.error,
        duration_ms=result.duration_ms,
    )


# === 端点 1: 触发 use case 全量 ===
@router.post(
    "/{uc_id}/trigger",
    response_model=UseCaseTriggerResponse,
    summary="Trigger a use case (full 6-stage pipeline or filtered stages)",
)
async def trigger_use_case(
    uc_id: str,
    req: UseCaseTriggerRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> UseCaseTriggerResponse:
    """业务入口: 按 use case 跑 6 阶段编排.

    示例:
        POST /api/v1/use-cases/shop-orders-mex-pipeline/trigger
        {}

        POST /api/v1/use-cases/shop-orders-mex-pipeline/trigger
        {"stages": [1, 5], "apply": true}

    与 /rescan 行为完全一致 (alias). 选 /trigger 还是 /rescan 看语义:
      - /trigger — "首次加载" / "我改了 YAML, 帮我跑一次"
      - /rescan  — "上游数据可能变了, 再扫一遍" (业务心智)
    """
    return await _trigger_uc(uc_id, req or UseCaseTriggerRequest(), db)


# === 端点 2: rescan (语义别名) ===
@router.post(
    "/{uc_id}/rescan",
    response_model=UseCaseTriggerResponse,
    summary="Re-scan a use case (alias of /trigger, idempotent)",
)
async def rescan_use_case(
    uc_id: str,
    req: UseCaseTriggerRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> UseCaseTriggerResponse:
    """运维 / UI 入口: 重扫 use case (idempotent)."""
    return await _trigger_uc(uc_id, req or UseCaseTriggerRequest(), db)


# === 端点 3: 单阶段触发 ===
@router.post(
    "/{uc_id}/stages/{stage}/trigger",
    response_model=UseCaseTriggerResponse,
    summary="Trigger a single stage (1..6) of a use case",
)
async def trigger_stage(
    uc_id: str,
    stage: int,
    req: UseCaseStageRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> UseCaseTriggerResponse:
    """单阶段: 只想重扫阶段 3 (MEX), 调此端点."""
    if not 1 <= stage <= 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"stage must be 1..6, got {stage}",
        )
    return await _trigger_uc(
        uc_id,
        UseCaseTriggerRequest(stages=[stage], dry_run=req.dry_run if req else False, apply=True),
        db,
    )
