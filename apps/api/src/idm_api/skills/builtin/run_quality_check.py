"""run_quality_check: 跑 quality_rule 并写 quality_result (含 5 种 rule_type).

Inputs:
    rule_id: str           跑这一条规则
    table_id: str          跑该表所有启用的规则
    apply: bool             True=写 quality_result, False=仅返回

支持的 rule_type:
    - freshness   : 检查 max(created_at) < now - threshold
    - volume       : row_count 在 [min, max] 区间
    - null_ratio   : 列 null_ratio < threshold
    - distinct     : distinct_count >= threshold
    - custom       : 用户给 SQL, 期望结果 0 行 (异常)

Outputs (SkillOutput.items):
    [{rule_id, table_id, passed, observed_value, threshold, message, duration_ms}, ...]
"""
from __future__ import annotations

import logging
import re
import time
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.skills.mcp import get_clickhouse_mcp
from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill
from idm_kg.models.column_asset import ColumnAsset
from idm_kg.models.quality import QualityResult, QualityRule
from idm_kg.models.table_asset import TableAsset

logger = logging.getLogger(__name__)


_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _qi(name: str) -> str:
    if not _SAFE_IDENT.match(name):
        return f'"{name.replace(chr(34), chr(34) * 2)}"'
    return f"`{name}`"


async def _exec_check(rule: QualityRule, table: TableAsset, mcp) -> dict[str, Any]:
    """根据 rule_type 跑具体检查. 返回 quality_result 字段 dict."""
    parts = (table.fqn or "").split(".")
    if len(parts) != 4:
        return {"passed": False, "message": "bad fqn", "observed": None, "threshold": None}
    _, db_name, _, tbl_name = parts
    full = f"{_qi(db_name)}.{_qi(tbl_name)}"
    rule_def = rule.definition or {}
    started = time.perf_counter()

    if rule.rule_type == "freshness":
        col = rule_def.get("column") or "created_at"
        if not _SAFE_IDENT.match(col):
            return {"passed": False, "message": f"bad col: {col}", "observed": None, "threshold": None}
        threshold_min = int(rule_def.get("threshold_minutes") or 60)
        sql = (
            f"SELECT max({_qi(col)}) AS last_ts, "
            f"now() - max({_qi(col)}) AS age "
            f"FROM {full}"
        )
        try:
            rows = mcp.run_query(sql)
        except Exception as e:  # noqa: BLE001
            return {"passed": False, "message": f"sql: {e}", "observed": None, "threshold": threshold_min}
        if not rows or rows[0].get("last_ts") is None:
            return {"passed": False, "message": "no rows", "observed": None, "threshold": threshold_min}
        age_min = rows[0].get("age")
        passed = age_min is not None and age_min <= threshold_min
        return {
            "passed": passed,
            "observed": age_min,
            "threshold": threshold_min,
            "message": f"last {col} {age_min}m ago (limit {threshold_min}m)",
        }

    if rule.rule_type == "volume":
        stats = mcp.get_table_stats(db_name, tbl_name)
        row_count = int(stats.get("row_count") or 0)
        mn = rule_def.get("min")
        mx = rule_def.get("max")
        passed = True
        if mn is not None and row_count < mn:
            passed = False
        if mx is not None and row_count > mx:
            passed = False
        return {
            "passed": passed,
            "observed": row_count,
            "threshold": mn if mn is not None else mx,
            "message": f"row_count={row_count} expected {mn}~{mx}",
        }

    if rule.rule_type == "null_ratio":
        col = rule_def.get("column")
        threshold = float(rule_def.get("threshold") or 0.5)
        if not col or not _SAFE_IDENT.match(col):
            return {"passed": False, "message": "bad col", "observed": None, "threshold": threshold}
        sql = f"SELECT countIf({_qi(col)} IS NULL OR {_qi(col)} = '') AS n, count() AS t FROM {full}"
        try:
            rows = mcp.run_query(sql)
        except Exception as e:  # noqa: BLE001
            return {"passed": False, "message": f"sql: {e}", "observed": None, "threshold": threshold}
        n = int(rows[0].get("n") or 0) if rows else 0
        t = int(rows[0].get("t") or 0) if rows else 0
        ratio = (n / t) if t > 0 else 0.0
        passed = ratio <= threshold
        return {
            "passed": passed,
            "observed": round(ratio, 4),
            "threshold": threshold,
            "message": f"null_ratio={ratio:.3f} limit {threshold}",
        }

    if rule.rule_type == "distinct":
        col = rule_def.get("column")
        threshold = int(rule_def.get("min") or 1)
        if not col or not _SAFE_IDENT.match(col):
            return {"passed": False, "message": "bad col", "observed": None, "threshold": threshold}
        sql = f"SELECT uniqExact({_qi(col)}) AS d FROM {full}"
        try:
            rows = mcp.run_query(sql)
        except Exception as e:  # noqa: BLE001
            return {"passed": False, "message": f"sql: {e}", "observed": None, "threshold": threshold}
        d = int(rows[0].get("d") or 0) if rows else 0
        passed = d >= threshold
        return {
            "passed": passed,
            "observed": d,
            "threshold": threshold,
            "message": f"distinct={d} min {threshold}",
        }

    if rule.rule_type == "custom":
        sql = rule_def.get("sql")
        if not sql:
            return {"passed": False, "message": "missing sql", "observed": None, "threshold": 0}
        try:
            rows = mcp.run_query(sql)
        except Exception as e:  # noqa: BLE001
            return {"passed": False, "message": f"sql: {e}", "observed": None, "threshold": 0}
        passed = len(rows) == 0
        return {
            "passed": passed,
            "observed": len(rows),
            "threshold": 0,
            "message": f"custom check returned {len(rows)} rows",
        }

    return {"passed": False, "message": f"unknown rule_type: {rule.rule_type}", "observed": None, "threshold": None}


@skill(name="run_quality_check", version=1, agent="quality")
async def run_quality_check(ctx: SkillContext, **inputs: Any) -> SkillResult:
    rule_id: str = inputs.get("rule_id") or ""
    table_id: str = inputs.get("table_id") or ""
    apply: bool = bool(inputs.get("apply", True))

    if ctx.db is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.db is None")

    # 1) 选规则
    stmt = select(QualityRule).where(QualityRule.is_enabled.is_(True))
    if rule_id:
        try:
            stmt = stmt.where(QualityRule.id == _uuid.UUID(rule_id))
        except ValueError:
            return SkillResult(ok=False, output=SkillOutput(), error=f"bad rule_id: {rule_id}")
    elif table_id:
        try:
            stmt = stmt.where(QualityRule.table_id == _uuid.UUID(table_id))
        except ValueError:
            return SkillResult(ok=False, output=SkillOutput(), error=f"bad table_id: {table_id}")
    rules = list((await ctx.db.execute(stmt.limit(200))).scalars().all())
    if not rules:
        return SkillResult(
            ok=True,
            output=SkillOutput(items=[], summary={"reason": "no rules matched"}),
        )

    mcp = ctx.mcp.get("clickhouse") or get_clickhouse_mcp()
    if mcp is None:
        return SkillResult(ok=False, output=SkillOutput(), error="clickhouse MCP unavailable")

    items: list[dict[str, Any]] = []
    passed = failed = 0

    for rule in rules:
        table = await ctx.db.get(TableAsset, rule.table_id)
        if table is None:
            items.append({"rule_id": str(rule.id), "status": "skipped", "message": "table missing"})
            continue
        started = time.perf_counter()
        try:
            res = await _exec_check(rule, table, mcp)
        except Exception as e:  # noqa: BLE001
            res = {"passed": False, "message": f"exception: {e}", "observed": None, "threshold": None}
        duration_ms = int((time.perf_counter() - started) * 1000)
        if res.get("passed"):
            passed += 1
        else:
            failed += 1

        if apply:
            ctx.db.add(
                QualityResult(
                    rule_id=rule.id,
                    passed=bool(res.get("passed")),
                    observed_value=res.get("observed"),
                    threshold=res.get("threshold"),
                    message=(res.get("message") or "")[:1024],
                    extra={"duration_ms": duration_ms},
                    duration_ms=duration_ms,
                )
            )
        items.append(
            {
                "rule_id": str(rule.id),
                "table_id": str(rule.table_id),
                "fqn": table.fqn,
                "passed": res.get("passed"),
                "observed_value": res.get("observed"),
                "threshold": res.get("threshold"),
                "message": res.get("message"),
                "duration_ms": duration_ms,
            }
        )

    if apply:
        await ctx.db.commit()

    summary = {
        "rules_checked": len(rules),
        "passed": passed,
        "failed": failed,
        "apply": apply,
    }
    return SkillResult(
        ok=True,
        output=SkillOutput(items=items, summary=summary),
    )
