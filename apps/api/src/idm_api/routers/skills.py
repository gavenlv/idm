"""/api/v1/skills: Skill 执行 + 注册表查询.

POST /api/v1/skills/run          跑一个 Skill (name + inputs)
GET  /api/v1/skills              列出所有已注册 Skill
GET  /api/v1/skills/mcp/health   MCP sidecar 健康检查
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.db import get_db
from idm_api.skills.mcp import get_clickhouse_mcp
from idm_api.skills.runner import list_skills, run_skill

router = APIRouter()


class SkillRunRequest(BaseModel):
    name: str = Field(..., description="Skill 名称, 如 discover_clickhouse_assets")
    inputs: dict[str, Any] = Field(default_factory=dict)
    use_case_id: str | None = None
    dry_run: bool = False


class SkillRunResponse(BaseModel):
    ok: bool
    skill: str
    output: dict[str, Any]
    error: str | None = None
    duration_ms: int
    trace: list[dict[str, Any]] = []


@router.get("", summary="List registered skills")
async def get_skills() -> dict[str, Any]:
    return {"items": await list_skills()}


@router.post("/run", response_model=SkillRunResponse, summary="Run a skill")
async def post_run_skill(
    req: SkillRunRequest,
    db: AsyncSession = Depends(get_db),
) -> SkillRunResponse:
    try:
        result = await run_skill(
            req.name,
            inputs=req.inputs,
            use_case_id=req.use_case_id,
            dry_run=req.dry_run,
            db=db,
        )
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    return SkillRunResponse(
        ok=result.ok,
        skill=req.name,
        output=result.output.model_dump(),
        error=result.error,
        duration_ms=result.duration_ms,
        trace=[],  # trace 暂不返回, M1.5 持久化到 ai_skill_runs
    )


@router.get("/mcp/health", summary="MCP sidecar health")
async def mcp_health() -> dict[str, Any]:
    checks: dict[str, Any] = {}
    try:
        checks["clickhouse"] = get_clickhouse_mcp().health()
    except Exception as e:  # noqa: BLE001
        checks["clickhouse"] = {"status": "error", "error": str(e)[:200]}
    return {"checks": checks, "all_ok": all(c.get("status") == "ok" for c in checks.values())}
