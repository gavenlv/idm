"""/api/v1/scan/*  — 系统级 rescan 端点 (不依赖 use case).

适用场景:
  - 平台 onboarding: "刚接了一个 GCS bucket, 帮我扫一下"
  - 失败恢复: "ClickHouse 重新连上了, 扫一遍"
  - 周期任务: CronJob 定时 rescan 全部
  - ChatOps: Slack /bot idm-rescan gcs --bucket=foo

设计:
  - 路由直接调对应 skill, 不走 use case
  - 一次调用可以多 stage (如 GCS: stage=1,2,4)
  - 输出按 subtype 计数
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.db import get_db
from idm_api.schemas import RescanAssetRequest, RescanAssetResponse
from idm_api.skills.runner import run_skill

router = APIRouter()


@router.post(
    "/asset",
    response_model=RescanAssetResponse,
    summary="System-wide re-scan by source type (no use case required)",
)
async def rescan_assets(
    req: RescanAssetRequest,
    db: AsyncSession = Depends(get_db),
) -> RescanAssetResponse:
    """按 source_type 重扫一组资产.

    示例:
        # 扫一个 GCS bucket 的所有阶段 (1=raw, 2=model-input, 4=model-output)
        POST /api/v1/scan/asset
        {"source_type": "gcs", "bucket": "company-raw"}

        # 扫 ClickHouse 整个 shop database
        POST /api/v1/scan/asset
        {"source_type": "clickhouse", "database": "shop"}

        # 扫 Superset (按 service_name)
        POST /api/v1/scan/asset
        {"source_type": "superset_export", "service_name": "superset-demo"}

        # 全部 (GCS + CH + Superset, 用注册的发现源)
        POST /api/v1/scan/asset
        {"source_type": "all"}
    """
    started = time.perf_counter()
    items_total = 0
    by_subtype: dict[str, int] = {}
    out_blocks: list[dict[str, Any]] = []
    had_error: str | None = None

    async def _run(skill: str, inputs: dict[str, Any]) -> dict[str, Any]:
        result = await run_skill(skill, inputs=inputs, dry_run=req.dry_run, db=db)
        return {
            "ok": result.ok,
            "items": list(result.output.items or []),
            "summary": result.output.summary or {},
            "error": result.error,
        }

    # === GCS 扫描 ===
    if req.source_type in ("gcs", "all") and (req.bucket or req.source_type == "all"):
        bucket = req.bucket or "company-raw"  # 'all' 模式给一个默认
        if bucket:
            for stage, role in [(1, "raw"), (2, "model_input"), (4, "model_output")]:
                blk = await _run("discover_gcs_assets", {
                    "bucket": bucket, "stage": stage, "source_role": role, "apply": not req.dry_run,
                })
                out_blocks.append({"stage": stage, "bucket": bucket, **blk})
                items_total += len(blk["items"])
                by_subtype["gcs_object"] = by_subtype.get("gcs_object", 0) + len(blk["items"])
                if not blk["ok"]:
                    had_error = blk["error"]

    # === ClickHouse 扫描 ===
    if req.source_type in ("clickhouse", "all") and (req.database or req.source_type == "all"):
        database = req.database or "shop"
        blk = await _run("discover_clickhouse_assets", {"database": database})
        out_blocks.append({"database": database, **blk})
        items_total += len(blk["items"])
        by_subtype["clickhouse_table"] = by_subtype.get("clickhouse_table", 0) + len(blk["items"])
        if not blk["ok"]:
            had_error = blk["error"]

    # === Superset 扫描 ===
    if req.source_type in ("superset_export", "superset_db", "all"):
        service = req.service_name or "superset-demo"
        blk = await _run("parse_superset_dashboard", {
            "stage": 6, "service_name": service, "apply": not req.dry_run,
        })
        out_blocks.append({"service_name": service, **blk})
        items_total += len(blk["items"])
        by_subtype["superset_dashboard"] = by_subtype.get("superset_dashboard", 0) + len(blk["items"])
        if not blk["ok"]:
            had_error = blk["error"]

    return RescanAssetResponse(
        ok=had_error is None,
        source_type=req.source_type,
        items_count=items_total,
        by_subtype=by_subtype,
        output={"blocks": out_blocks},
        error=had_error,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
