"""Skill runner: 执行一个 Skill, 捕获 trace / 错误 / 性能。

输入: skill_name + inputs
输出: SkillResult (含 trace, duration, output)

后续可扩展:
- 重试 (Skill 声明 max_retries)
- 暂停 / 恢复 (KG 表 ai_skill_runs 持久化状态)
- 并发 (Skill 间无依赖时)
- Eval Hook (LLM-as-judge, 离线 gold set)
"""
from __future__ import annotations

import logging
import time
from typing import Any

from idm_api.skills.llm import get_llm_router
from idm_api.skills.mcp import get_clickhouse_mcp
from idm_api.skills.registry import (
    SkillContext,
    SkillInput,
    SkillOutput,
    SkillResult,
    get_registry,
)

logger = logging.getLogger(__name__)


async def run_skill(
    skill_name: str,
    inputs: dict[str, Any] | None = None,
    *,
    use_case_id: str | None = None,
    dry_run: bool = False,
    db: Any = None,
) -> SkillResult:
    """执行一个已注册的 Skill."""
    version, agent, handler = get_registry().get(skill_name)
    inputs = inputs or {}
    started = time.perf_counter()

    ctx = SkillContext(
        db=db,
        llm=get_llm_router(),
        mcp={"clickhouse": get_clickhouse_mcp()},
        use_case_id=use_case_id or inputs.get("use_case_id"),
        dry_run=dry_run or inputs.get("dry_run", False),
    )
    ctx.log("start", skill=skill_name, version=version, agent=agent, inputs=inputs)

    try:
        # 验证输入 (用通用 SkillInput, 具体 Skill 可在 handler 内再校验)
        SkillInput(use_case_id=ctx.use_case_id, dry_run=ctx.dry_run)
        result = await handler(ctx, **inputs)
        if not isinstance(result, SkillResult):
            # 兜底: 允许 handler 返回 dict / SkillOutput
            if isinstance(result, SkillOutput):
                result = SkillResult(ok=True, output=result)
            elif isinstance(result, dict):
                result = SkillResult(ok=True, output=SkillOutput(**result))
            else:
                result = SkillResult(ok=False, output=SkillOutput(), error=f"bad return: {type(result)}")
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        ctx.log("end", ok=result.ok, duration_ms=result.duration_ms)
        return result
    except Exception as e:  # noqa: BLE001
        logger.exception("Skill %s failed", skill_name)
        return SkillResult(
            ok=False,
            output=SkillOutput(),
            error=f"{type(e).__name__}: {e}",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


async def list_skills() -> list[dict[str, Any]]:
    return get_registry().list()
