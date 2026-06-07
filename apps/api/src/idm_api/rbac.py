"""rbac: 极简 RBAC (M5 起步).

设计:
- 起步 4 种 role: viewer / steward / owner / admin
- header X-IDM-Actor + X-IDM-Roles 透传身份 (无 SSO 时)
- 后续接 OIDC 时, 把 header 替换为 JWT claim
- 所有写操作需要 steward+ 角色
- approve_suggestion / save_use_case 需要 owner+
- create / delete 需要 admin

辅助:
    ActorDep = Depends(current_actor)
    require_role("admin")(actor) -> Actor
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from fastapi import Depends, Header, HTTPException, status


@dataclass
class Actor:
    email: str
    roles: frozenset[str]
    tenant_id: str = "default"

    def has_role(self, *need: str) -> bool:
        return any(r in self.roles for r in need)


def _parse_roles(raw: str | None) -> frozenset[str]:
    if not raw:
        return frozenset({"viewer"})
    return frozenset(r.strip().lower() for r in raw.split(",") if r.strip())


def current_actor(
    x_idm_actor: str | None = Header(default=None, alias="X-IDM-Actor"),
    x_idm_roles: str | None = Header(default=None, alias="X-IDM-Roles"),
    x_idm_tenant: str | None = Header(default=None, alias="X-IDM-Tenant"),
) -> Actor:
    """从 header 拿身份; 无 header 时降级为匿名 viewer."""
    email = (x_idm_actor or "anonymous@local").strip().lower()
    roles = _parse_roles(x_idm_roles)
    tenant = (x_idm_tenant or "default").strip().lower()
    return Actor(email=email, roles=roles, tenant_id=tenant)


ActorDep = Depends(current_actor)


def require_role(*roles: str):
    """依赖工厂: 用于路径函数 / service 函数."""
    allowed = set(roles)

    def _dep(actor: Actor = ActorDep) -> Actor:
        if not actor.has_role(*allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"actor '{actor.email}' lacks one of roles: {sorted(allowed)}",
            )
        return actor

    return Depends(_dep)


def assert_role(actor: Actor, *roles: str) -> None:
    """service 函数 / BDD 步骤里直接调用的 helper."""
    if not actor.has_role(*roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"actor '{actor.email}' lacks one of roles: {sorted(roles)}",
        )
