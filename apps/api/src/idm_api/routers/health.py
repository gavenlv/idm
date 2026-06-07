"""/health: 健康检查 (liveness / readiness / 详细)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.config import Settings, get_settings
from idm_api.db import get_db

router = APIRouter()


@router.get("", summary="Liveness probe")
async def liveness() -> dict[str, Any]:
    """进程存活检查 (K8s livenessProbe)."""
    return {"status": "ok"}


@router.get("/ready", summary="Readiness probe")
async def readiness(
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """就绪检查 (K8s readinessProbe): DB 可达 + 关键依赖 OK."""
    checks: dict[str, str] = {}

    # DB
    try:
        result = await db.execute(text("SELECT 1"))
        result.scalar()
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    # ClickHouse (只做 TCP 检查, 不打 SQL)
    try:
        import socket

        with socket.create_connection(
            (settings.clickhouse_host, settings.clickhouse_port), timeout=2
        ):
            checks["clickhouse"] = "ok"
    except Exception as e:
        checks["clickhouse"] = f"warn: {e}"

    # 总体状态
    all_ok = all(v == "ok" for v in checks.values())
    critical_ok = checks.get("database") == "ok"

    return {
        "status": "ok" if all_ok else "degraded" if critical_ok else "down",
        "env": settings.app_env,
        "version": "0.1.0",
        "checks": checks,
    }


@router.get("/info", summary="Service info")
async def info(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """服务信息 (供 /api/v1/* 公共展示)."""
    return {
        "service": settings.app_name,
        "env": settings.app_env,
        "version": "0.1.0",
        "planner_model": settings.idm_llm_planner_model,
        "default_model": settings.idm_llm_default_model,
        "cheap_model": settings.idm_llm_cheap_model,
        "local_model": settings.idm_llm_local_model,
    }
