"""idm_api.skills.utils: 跨 Skill 复用的工具.

- upsert_table_asset: 按 service 隔离的 table_asset upsert (避免跨 service FQN 冲突)
- normalize_fqn: 标准化 FQN (lowercase, trim)
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from idm_kg.models.table_asset import TableAsset

logger = logging.getLogger(__name__)


def normalize_fqn(fqn: str) -> str:
    """FQN 标准化: 全部小写, 去空格."""
    return (fqn or "").strip().lower()


async def ensure_service(db: AsyncSession, service_name: str, service_type: str = "external") -> str:
    """按 name 找 Service, 不存在就建. 返回 service.id (UUID str)."""
    from idm_kg.models.service import Service

    svc = (
        await db.execute(select(Service).where(Service.name == service_name))
    ).scalar_one_or_none()
    if svc is None:
        svc = Service(name=service_name, type=service_type, description=f"Auto-created by skill for {service_name}")
        db.add(svc)
        await db.flush()
    return str(svc.id)


async def upsert_table_asset(
    db: AsyncSession,
    *,
    fqn: str,
    name: str,
    service_id: str | None = None,
    schema_id: str | None = None,
    asset_type: str = "table",
    description: str | None = None,
    extra: dict[str, Any] | None = None,
    tier: str = "normal",
    status: str = "active",
) -> str:
    """服务隔离的 table_asset upsert: FQN 必须以 service.name 起头.
    返回 asset.id (UUID str).

    service_id 是可选的: 若提供, 校验 FQN 以 service.name 起头; 否则只做格式校验.
    TableAsset 模型本身没有 service_id 列, service 信息从 fqn 前缀推断.
    """
    fqn = normalize_fqn(fqn)

    # 服务隔离校验
    if service_id:
        from idm_kg.models.service import Service

        svc = await db.get(Service, service_id)
        if svc is None:
            raise ValueError(f"Service id={service_id} not found")
        if not fqn.startswith(svc.name + "."):
            raise ValueError(
                f"FQN '{fqn}' must start with service.name '{svc.name}.' (service isolation)"
            )

    # schema_id 必填: 没传就从 service.name 下找/建一个 default schema
    if not schema_id:
        schema_id = await _ensure_default_schema(db, fqn=fqn, service_id=service_id)

    # upsert (按 fqn 唯一)
    values: dict[str, Any] = {
        "fqn": fqn,
        "name": name,
        "schema_id": schema_id,
        "asset_type": asset_type,
        "tier": tier,
        "status": status,
        "description": description,
        "extra": extra or {},
    }

    stmt = (
        pg_insert(TableAsset)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[TableAsset.fqn],
            set_={
                "name": name,
                "schema_id": schema_id,
                "asset_type": asset_type,
                "tier": tier,
                "status": status,
                "description": description,
                "extra": extra or {},
            },
        )
        .returning(TableAsset.id)
    )
    row = (await db.execute(stmt)).first()
    return str(row[0]) if row else ""


async def _ensure_default_schema(db: AsyncSession, *, fqn: str, service_id: str | None) -> str:
    """根据 FQN 解析出 schema_id (service.db.schema.table), 找不到就建.

    解析规则:
      - fqn = svc.db.schema.table  → 4 段, schema 是第 3 段
      - fqn = svc.schema.table     → 3 段, schema 是第 2 段
      - 其它: 找 service 下 name='default' 的 schema
    """
    from idm_kg.models.schema import Schema
    from idm_kg.models.database import Database
    from idm_kg.models.service import Service
    from sqlalchemy import select

    parts = fqn.split(".")
    if len(parts) >= 4:
        svc_name, db_name, sch_name = parts[0], parts[1], parts[2]
    elif len(parts) == 3:
        svc_name, sch_name = parts[0], parts[2]
        db_name = "default"
    else:
        svc_name = parts[0] if parts else "default"
        db_name = "default"
        sch_name = "default"

    # 1) Service
    svc = (await db.execute(select(Service).where(Service.name == svc_name))).scalar_one_or_none()
    if svc is None:
        svc = Service(name=svc_name, type="external", description=f"Auto-created for {svc_name}")
        db.add(svc)
        await db.flush()

    # 2) Database
    d = (await db.execute(select(Database).where(Database.service_id == svc.id, Database.name == db_name))).scalar_one_or_none()
    if d is None:
        d = Database(service_id=svc.id, name=db_name, description=f"Auto-created db {db_name}")
        db.add(d)
        await db.flush()

    # 3) Schema
    s = (await db.execute(select(Schema).where(Schema.database_id == d.id, Schema.name == sch_name))).scalar_one_or_none()
    if s is None:
        s = Schema(database_id=d.id, name=sch_name, description=f"Auto-created schema {sch_name}")
        db.add(s)
        await db.flush()
    return str(s.id)


__all__ = ["ensure_service", "normalize_fqn", "upsert_table_asset"]
