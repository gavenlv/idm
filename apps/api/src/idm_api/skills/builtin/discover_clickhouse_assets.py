"""discover_clickhouse_assets: 从 ClickHouse 发现表 + 列, 写入 table_assets / column_assets.

Inputs:
    database: str  目标 database (默认 .env 中的 clickhouse_database)
    include_tables: list[str]  白名单 (空 = 全部)
    exclude_tables: list[str]  黑名单 (支持前缀 *, 如 'tmp_*')
    sample_rows: int  每表采样行数 (默认 0, 不采)

Outputs (SkillOutput.items):
    [{table_id, fqn, columns_added: int, status: created|updated|skipped}, ...]

写入:
    Service (clickhouse) -> Database -> Schema (default) -> TableAsset + ColumnAsset
    FQN: <service>.<database>.<schema>.<table>
"""
from __future__ import annotations

import fnmatch
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill
from idm_kg.models.column_asset import ColumnAsset
from idm_kg.models.database import Database
from idm_kg.models.schema import Schema
from idm_kg.models.service import Service
from idm_kg.models.table_asset import TableAsset

logger = logging.getLogger(__name__)


# === helpers ===
def _match(name: str, includes: list[str], excludes: list[str]) -> bool:
    if includes and not any(fnmatch.fnmatch(name, p) for p in includes):
        return False
    if excludes and any(fnmatch.fnmatch(name, p) for p in excludes):
        return False
    return True


def _is_partition_key(flags: str | None) -> bool:
    return bool(flags and "PRIMARY KEY" not in (flags or "") and ("PARTITION" in (flags or "").upper()))


def _is_primary_key(flags: str | None) -> bool:
    return bool(flags and "PRIMARY KEY" in flags.upper())


def _is_nullable(type_str: str | None) -> bool:
    """ClickHouse: 类型后带 'Nullable(...)' 表示可空."""
    if not type_str:
        return True
    return type_str.strip().startswith("Nullable(")


def _strip_nullable(type_str: str | None) -> str:
    if not type_str:
        return "Unknown"
    s = type_str.strip()
    if s.startswith("Nullable(") and s.endswith(")"):
        return s[len("Nullable(") : -1]
    return s


async def _ensure_service_db_schema(
    db: AsyncSession,
    *,
    service_name: str,
    database_name: str,
    schema_name: str = "default",
) -> tuple[Service, Database, Schema]:
    """确保 service -> database -> schema 链路存在, 返回实体."""
    # Service
    svc = (
        await db.execute(select(Service).where(Service.name == service_name))
    ).scalar_one_or_none()
    if svc is None:
        svc = Service(name=service_name, type="clickhouse", description="ClickHouse via MCP")
        db.add(svc)
        await db.flush()

    # Database
    d = (
        await db.execute(
            select(Database).where(Database.service_id == svc.id, Database.name == database_name)
        )
    ).scalar_one_or_none()
    if d is None:
        d = Database(service_id=svc.id, name=database_name)
        db.add(d)
        await db.flush()

    # Schema
    s = (
        await db.execute(
            select(Schema).where(Schema.database_id == d.id, Schema.name == schema_name)
        )
    ).scalar_one_or_none()
    if s is None:
        s = Schema(database_id=d.id, name=schema_name)
        db.add(s)
        await db.flush()
    return svc, d, s


# === Skill ===
@skill(name="discover_clickhouse_assets", version=1, agent="schema")
async def discover_clickhouse_assets(ctx: SkillContext, **inputs: Any) -> SkillResult:
    database: str = inputs.get("database") or ""
    includes: list[str] = inputs.get("include_tables") or []
    excludes: list[str] = inputs.get("exclude_tables") or []
    sample_rows: int = int(inputs.get("sample_rows") or 0)
    service_name: str = inputs.get("service_name") or "clickhouse-prod"

    if not database:
        return SkillResult(
            ok=False,
            output=SkillOutput(),
            error="missing required input: 'database'",
        )
    if ctx.db is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.db is None")

    mcp = ctx.mcp.get("clickhouse")
    if mcp is None:
        return SkillResult(ok=False, output=SkillOutput(), error="clickhouse MCP not available")

    ctx.log("ch_list_tables", database=database)
    tables = mcp.list_tables(database)
    ctx.log("ch_list_tables_done", count=len(tables))

    items: list[dict[str, Any]] = []
    created = updated = skipped = 0

    svc, db_obj, schema_obj = await _ensure_service_db_schema(
        ctx.db, service_name=service_name, database_name=database
    )

    for tname in tables:
        if not _match(tname, includes, excludes):
            skipped += 1
            continue
        fqn = f"{svc.name}.{db_obj.name}.{schema_obj.name}.{tname}"

        # 1) 描述列
        try:
            descs = mcp.describe_table(database, tname)
        except Exception as e:  # noqa: BLE001
            ctx.log("ch_describe_error", table=tname, err=str(e))
            continue
        if not descs:
            skipped += 1
            continue

        # 2) 轻量统计
        try:
            stats = mcp.get_table_stats(database, tname)
        except Exception:  # noqa: BLE001
            stats = {"row_count": None, "size_bytes": None}

        # 3) 采样 (可选)
        samples: list[Any] = []
        if sample_rows > 0:
            try:
                samples = mcp.sample_rows(database, tname, limit=sample_rows)
            except Exception:  # noqa: BLE001
                samples = []

        # 4) 写 / 更新 TableAsset
        existing = (
            await ctx.db.execute(select(TableAsset).where(TableAsset.fqn == fqn))
        ).scalar_one_or_none()

        if existing is None:
            asset = TableAsset(
                schema_id=schema_obj.id,
                name=tname,
                fqn=fqn,
                asset_type="table",
                tier="normal",
                status="active",
                column_count=len(descs),
                row_count=stats.get("row_count"),
                size_bytes=stats.get("size_bytes"),
                last_profiled_at=None,
                extra={"mcp_source": "clickhouse", "service_id": str(svc.id)},
            )
            ctx.db.add(asset)
            await ctx.db.flush()
            table_status = "created"
            created += 1
            table_id = asset.id
        else:
            existing.column_count = len(descs)
            existing.row_count = stats.get("row_count")
            existing.size_bytes = stats.get("size_bytes")
            table_status = "updated"
            updated += 1
            table_id = existing.id
            # 清掉旧 columns, 重建
            cols_existing = (
                await ctx.db.execute(select(ColumnAsset).where(ColumnAsset.table_id == existing.id))
            ).scalars().all()
            for c in cols_existing:
                await ctx.db.delete(c)
            await ctx.db.flush()

        # 5) 写 ColumnAsset
        for idx, d in enumerate(descs):
            col_name = d.get("name")
            col_type = _strip_nullable(d.get("type"))
            flags = d.get("default_type") or ""
            # 采样本列
            col_samples = [row.get(col_name) for row in samples[:5] if isinstance(row, dict)]
            ctx.db.add(
                ColumnAsset(
                    table_id=table_id,
                    name=col_name,
                    ordinal=idx,
                    data_type=col_type,
                    nullable=_is_nullable(d.get("type")),
                    is_primary_key=_is_primary_key(flags),
                    is_partition_key=_is_partition_key(flags),
                    sample_values=col_samples,
                    extra={"raw_type": d.get("type"), "comment": d.get("comment")},
                )
            )
        await ctx.db.flush()

        items.append(
            {
                "table_id": str(table_id),
                "fqn": fqn,
                "columns_added": len(descs),
                "row_count": stats.get("row_count"),
                "size_bytes": stats.get("size_bytes"),
                "status": table_status,
            }
        )

    await ctx.db.commit()

    summary = {
        "database": database,
        "service": svc.name,
        "tables_total": len(tables),
        "created": created,
        "updated": updated,
        "skipped": skipped,
    }
    ctx.log("done", **summary)
    return SkillResult(
        ok=True,
        output=SkillOutput(items=items, summary=summary, artifacts=[i["table_id"] for i in items]),
    )
