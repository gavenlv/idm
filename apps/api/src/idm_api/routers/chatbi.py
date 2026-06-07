"""/api/v1/chatbi: 自然语言 -> SQL 入口 (NL2SQL skill 5 层 Guard).

M4 入口. 流程:
  POST /api/v1/chatbi { question, service, dry_run }
    -> run_skill("nl2sql", ...)
    -> 5 层 Guard 验证
    -> (非 dry_run) 通过 clickhouse MCP 执行
    -> 返回 SQL + 样本结果
"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.db import get_db
from idm_api.schemas import ChatBIRequest, ChatBIResponse
from idm_api.skills.mcp import get_clickhouse_mcp
from idm_api.skills.runner import run_skill

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("", response_model=ChatBIResponse, summary="ChatBI: ask in natural language")
async def ask(payload: ChatBIRequest, db: AsyncSession = Depends(get_db)) -> ChatBIResponse:
    """M4 ChatBI: 自然语言 -> SQL.

    简化: 走 nl2sql skill (含 5 层 Guard).
    实际执行: dry_run=False 时, 通过 ClickHouse MCP 跑 (只读账号 + LIMIT 强制).
    """
    started = time.perf_counter()
    inputs: dict[str, Any] = {
        "question": payload.question,
        "service": payload.service or "",
        "dry_run": payload.dry_run,
    }
    result = await run_skill("nl2sql", inputs, db=db)
    if not result.ok:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"nl2sql failed: {result.error}",
        )

    out = result.output
    summary = out.summary or {}
    sql = summary.get("sql")
    rationale = summary.get("rationale")
    confidence = float(summary.get("confidence") or 0.0)
    guard_warnings: list[str] = list(summary.get("guard_warnings") or [])
    chart_hint = summary.get("chart_hint")
    model = summary.get("model")

    sample: list[dict[str, Any]] = []
    if sql and not payload.dry_run:
        # 真实跑: ClickHouse MCP (受 SQL Guard)
        mcp = get_clickhouse_mcp()
        try:
            sample = mcp.run_query(sql)
        except Exception as e:  # noqa: BLE001
            guard_warnings.append(f"execute failed: {e}")

    duration_ms = int((time.perf_counter() - started) * 1000)
    return ChatBIResponse(
        question=payload.question,
        sql=sql,
        rationale=rationale,
        confidence=confidence,
        guard_warnings=guard_warnings,
        result_sample=sample[:20],
        chart_hint=chart_hint,
        duration_ms=duration_ms,
        model=model,
    )
