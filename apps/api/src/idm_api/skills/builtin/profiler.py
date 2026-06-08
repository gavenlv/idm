"""profiler: 给表/列打画像 (row_count / null_ratio / distinct_count / sample), 写 column_asset.

M4: 主动采样的画像引擎. 设计:
- 默认 SAMPLE 1000 行 (ClickHouse SAMPLE 1/100 或 LIMIT 1000)
- 写入 column_asset:
    null_ratio, distinct_count, sample_values (最多 10), last_profiled_at
- 写入 table_asset:
    row_count, last_profiled_at

Inputs:
    table_ids: list[str]   仅扫这些表 (空 = 全部 active)
    service: str           限定 service (空 = 全部)
    sample_rows: int       每表采样多少行 (默认 1000, 上限 10000)
    apply: bool            True=写库; False=仅计算

Outputs (SkillOutput.items):
    [{table_id, fqn, row_count, columns_profiled, duration_ms}, ...]
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.skills.mcp import get_clickhouse_mcp
from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill
from idm_kg.models.column_asset import ColumnAsset
from idm_kg.models.table_asset import TableAsset

logger = logging.getLogger(__name__)

_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _q(name: str) -> str:
    """安全引用 column/table identifier."""
    if not _SAFE_IDENT.match(name):
        # 退回转义; 不要在生产用, 走 ALTER-safe 的方式
        return f'"{name.replace(chr(34), chr(34) * 2)}"'
    return f"`{name}`"


@skill(name="profiler", version=1, agent="quality")
async def profiler(ctx: SkillContext, **inputs: Any) -> SkillResult:
    table_ids: list[str] = inputs.get("table_ids") or []
    service: str = inputs.get("service") or ""
    sample_rows: int = int(inputs.get("sample_rows") or 1000)
    apply: bool = bool(inputs.get("apply", True))

    if ctx.db is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.db is None")
    sample_rows = max(1, min(10000, sample_rows))

    # 1) 选表 (默认 active + 没 profile 过)
    stmt = select(TableAsset).where(TableAsset.status == "active")
    if table_ids:
        stmt = stmt.where(TableAsset.id.in_(table_ids))
    if service:
        stmt = stmt.where(TableAsset.fqn.like(f"{service}.%"))
    if not table_ids:
        stmt = stmt.where(TableAsset.last_profiled_at.is_(None))
    tables = list((await ctx.db.execute(stmt.limit(50))).scalars().all())
    if not tables:
        return SkillResult(
            ok=True,
            output=SkillOutput(items=[], summary={"reason": "no tables to profile"}),
        )

    mcp = ctx.mcp.get("clickhouse") or get_clickhouse_mcp()
    if mcp is None:
        return SkillResult(ok=False, output=SkillOutput(), error="clickhouse MCP unavailable")

    items: list[dict[str, Any]] = []
    skipped = 0

    for t in tables:
        started = time.perf_counter()
        try:
            parts = (t.fqn or "").split(".")
            # FQN may be 3-part (db.schema.tbl) or 4-part (svc.db.schema.tbl)
            if len(parts) == 3:
                db_name, _, tbl_name = parts
            elif len(parts) == 4:
                _, db_name, _, tbl_name = parts
            else:
                skipped += 1
                continue
            # 1) row_count
            try:
                stats = mcp.get_table_stats(db_name, tbl_name)
                row_count = int(stats.get("row_count") or 0)
            except Exception:  # noqa: BLE001
                row_count = 0

            # 2) 拉列
            cols = list(
                (
                    await ctx.db.execute(select(ColumnAsset).where(ColumnAsset.table_id == t.id))
                ).scalars()
            )
            if not cols:
                skipped += 1
                continue

            # 3) 采样
            samples: list[dict[str, Any]] = []
            try:
                samples = mcp.sample_rows(db_name, tbl_name, limit=sample_rows)
            except Exception:  # noqa: BLE001
                samples = []

            # 4) 算每列的 null_ratio / distinct_count / sample_values
            for c in cols:
                vals = [s.get(c.name) for s in samples if s and c.name in s]
                nulls = sum(1 for v in vals if v is None or v == "")
                if vals:
                    c.null_ratio = round(nulls / max(1, len(samples)), 3)
                    c.distinct_count = len({str(v) for v in vals if v is not None})
                    c.sample_values = [v for v in vals[:10]]
                if apply:
                    pass  # 后续 flush

            # 5) 写表统计
            if apply:
                t.row_count = row_count or t.row_count
                from datetime import datetime, timezone
                t.last_profiled_at = datetime.now(timezone.utc)
                ctx.db.add_all(cols)

            duration_ms = int((time.perf_counter() - started) * 1000)
            items.append(
                {
                    "table_id": str(t.id),
                    "fqn": t.fqn,
                    "row_count": row_count,
                    "columns_profiled": len(cols),
                    "duration_ms": duration_ms,
                    "status": "ok",
                }
            )
        except Exception as e:  # noqa: BLE001
            skipped += 1
            items.append(
                {
                    "table_id": str(t.id),
                    "fqn": t.fqn,
                    "status": "error",
                    "error": str(e)[:200],
                }
            )

    if apply:
        await ctx.db.commit()

    summary = {
        "tables_scanned": len(tables),
        "tables_profiled": sum(1 for i in items if i.get("status") == "ok"),
        "skipped": skipped,
        "apply": apply,
    }
    return SkillResult(
        ok=True,
        output=SkillOutput(items=items, summary=summary),
    )
