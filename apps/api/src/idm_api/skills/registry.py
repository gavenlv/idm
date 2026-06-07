"""Skill registry: 装饰器注册, name -> handler.

Skill 签名:
    @skill(name="discover_clickhouse_assets", version=1, agent="schema")
    async def run(ctx: SkillContext, **inputs) -> SkillResult: ...

- name: 全局唯一, 形如 "discover_clickhouse_assets"
- version: 整数, 升级时 +1
- agent: schema / lineage / doc / pii / owner / quality / insight / glossary
- inputs/outputs: 用 Pydantic model 描述 (M2 加)
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class SkillInput(BaseModel):
    """Skill 通用输入 (具体 Skill 可继承扩展)."""

    use_case_id: str | None = Field(default=None, description="所属 use case")
    dry_run: bool = Field(default=False, description="只跑不改 KG")


class SkillOutput(BaseModel):
    """Skill 通用输出."""

    items: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list, description="写到 KG 的 entity id 列表")


@dataclass
class SkillContext:
    """运行时上下文: DB session + LLM + MCP + trace 句柄."""

    db: Any = None  # AsyncSession
    llm: Any = None  # LLMRouter
    mcp: dict[str, Any] = field(default_factory=dict)  # name -> client
    trace: list[dict[str, Any]] = field(default_factory=list)
    use_case_id: str | None = None
    dry_run: bool = False

    def log(self, kind: str, **payload: Any) -> None:
        self.trace.append({"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, **payload})


@dataclass
class SkillResult:
    ok: bool
    output: SkillOutput
    error: str | None = None
    duration_ms: int = 0


# === Decorator / Registry ===
SkillHandler = Callable[..., Awaitable[SkillResult]]


class _Registry:
    def __init__(self) -> None:
        self._items: dict[str, tuple[int, str, SkillHandler]] = {}

    def register(
        self,
        name: str,
        version: int,
        agent: str,
        handler: SkillHandler,
    ) -> None:
        if name in self._items:
            existing = self._items[name]
            if existing[0] >= version:
                raise ValueError(
                    f"Skill '{name}' v{version} cannot downgrade existing v{existing[0]}"
                )
        self._items[name] = (version, agent, handler)

    def get(self, name: str) -> tuple[int, str, SkillHandler]:
        if name not in self._items:
            raise KeyError(f"Skill '{name}' not registered")
        return self._items[name]

    def list(self) -> list[dict[str, Any]]:
        return [
            {"name": n, "version": v, "agent": a}
            for n, (v, a, _) in sorted(self._items.items())
        ]


_REGISTRY = _Registry()


def skill(name: str, version: int = 1, agent: str = "core") -> Callable[[SkillHandler], SkillHandler]:
    """装饰器: 注册一个 Skill handler."""

    def deco(handler: SkillHandler) -> SkillHandler:
        _REGISTRY.register(name, version, agent, handler)
        handler.__idm_skill__ = {"name": name, "version": version, "agent": agent}  # type: ignore[attr-defined]
        return handler

    return deco


def get_registry() -> _Registry:
    return _REGISTRY


# === 公共类型导出 ===
Skill = _Registry  # alias
__all__ = ["Skill", "SkillContext", "SkillInput", "SkillOutput", "SkillResult", "skill"]
