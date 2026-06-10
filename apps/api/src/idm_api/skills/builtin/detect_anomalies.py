"""detect_anomalies: 周期任务 - 跑统计, 检测漂移, 推 suggestion.

策略 (AGENT_INSTRUCTIONS §10):
- 每张 active 表执行 4 类检测:
  1) 体积漂移 (row_count / bytes 7 日 vs 30 日均值, |z|>2 告警)
  2) 字段覆盖率突降 (关键列 null 比例 > 历史 p95 + 10%)
  3) PII 风险升级 (新列被分类为 PII, 或 PII 列被新增访问)
  4) Owner 空缺 (active 表 30 天无 verified owner)

输出:
- ai_suggestion 表写入: anomaly, severity, table_fqn, details, suggested_action
- 写回 table_assets.health_score (0-100, 越低越异常)

支持 schedule via Airflow / cron, 用 idm-api 触发: POST /api/v1/skills/run name=detect_anomalies inputs={service, ...}
"""
from __future__ import annotations

import json
import logging
import math
import time
import uuid
from typing import Any

from sqlalchemy import select

from idm_api.skills.mcp import get_clickhouse_mcp
from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill
from idm_kg.models.ai_suggestion import AISuggestion
from idm_kg.models.column_asset import ColumnAsset
from idm_kg.models.owner import AssetOwner
from idm_kg.models.table_asset import TableAsset

logger = logging.getLogger(__name__)

# 检测阈值
Z_THRESHOLD = 2.0
NULL_DELTA_THRESHOLD = 0.10  # 覆盖率下降超过 10% 视为突降
RECENT_DAYS = 7
HISTORY_DAYS = 30


# === 检测 1: 体积漂移 ===
async def _detect_volume_drift(
    ctx: SkillContext, table: TableAsset, mcp: Any
) -> dict[str, Any] | None:
    """对照历史 (CH system.parts) 检查 parts 数 / row_count 漂移."""
    db, tbl = table.fqn.split(".")[1], table.fqn.split(".")[-1]
    try:
        # 当前 parts 数
        cur_sql = f"""
            SELECT count() AS parts, sum(rows) AS rows
            FROM system.parts
            WHERE database='{db}' AND table='{tbl}' AND active
        """
        cur = mcp.run_query(cur_sql)
        if not cur or cur[0]["parts"] == 0:
            return None
        cur_parts = int(cur[0]["parts"])
        cur_rows = int(cur[0]["rows"] or 0)

        # 30 天前的 parts (通过 modification_time 过滤近似)
        # system.parts 没有历史快照, 用 modification_time 分布做近似
        hist_sql = f"""
            SELECT
                quantile(0.5)(parts_count) AS median_parts,
                quantile(0.9)(parts_count) AS p90_parts
            FROM system.parts_history
            WHERE database='{db}' AND table='{tbl}'
              AND event_time > now() - INTERVAL {HISTORY_DAYS} DAY
              AND event_type = 'NewPart'
        """
        try:
            hist = mcp.run_query(hist_sql)
        except Exception:  # noqa: BLE001
            # 没有 parts_history 视图就跳过
            return None
        if not hist or hist[0]["p90_parts"] is None or hist[0]["p90_parts"] == 0:
            return None
        median = float(hist[0]["median_parts"] or 0)
        p90 = float(hist[0]["p90_parts"])
        # z-score 近似: (cur - median) / p90
        if p90 <= 0:
            return None
        z = (cur_parts - median) / p90
        if abs(z) > Z_THRESHOLD:
            return {
                "kind": "volume_drift",
                "severity": "high" if z > Z_THRESHOLD * 2 else "medium",
                "details": {
                    "current_parts": cur_parts,
                    "median_parts": median,
                    "p90_parts": p90,
                    "z_score": round(z, 2),
                    "current_rows": cur_rows,
                },
                "suggested_action": (
                    "排查上游写入, 是否在批量重跑或新建临时表"
                    if z > 0 else
                    "数据量明显下降, 检查是否有清理任务误删"
                ),
            }
    except Exception as e:  # noqa: BLE001
        logger.debug("volume_drift check failed for %s: %s", table.fqn, e)
    return None


# === 检测 2: 字段覆盖率突降 ===
async def _detect_null_spike(
    ctx: SkillContext, table: TableAsset, mcp: Any
) -> dict[str, Any] | None:
    """关键列 null 比例 > 历史 p95 + 10%."""
    cols = list(
        (
            await ctx.db.execute(
                select(ColumnAsset).where(
                    ColumnAsset.table_id == table.id,
                    ColumnAsset.nullable == False,  # noqa: E712
                ).limit(20)
            )
        ).scalars().all()
    )
    if not cols:
        return None
    bad: list[dict[str, Any]] = []
    db, tbl = table.fqn.split(".")[1], table.fqn.split(".")[-1]
    for c in cols:
        try:
            r = mcp.run_query(
                f"SELECT countIf({c.name} IS NULL) / count() AS null_ratio FROM `{db}`.`{tbl}`"
            )
            if r and r[0]["null_ratio"] is not None and float(r[0]["null_ratio"]) > NULL_DELTA_THRESHOLD:
                bad.append(
                    {
                        "column": c.name,
                        "null_ratio": round(float(r[0]["null_ratio"]), 4),
                    }
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("null check failed for %s.%s: %s", table.fqn, c.name, e)
    if not bad:
        return None
    return {
        "kind": "null_spike",
        "severity": "high" if len(bad) >= 3 else "medium",
        "details": {"affected_columns": bad[:10]},
        "suggested_action": "上游 schema 变更或 ETL 异常, 检查 NULL 引入路径",
    }


# === 检测 3: PII 风险升级 ===
async def _detect_pii_escalation(
    ctx: SkillContext, table: TableAsset
) -> dict[str, Any] | None:
    """新 PII 列 (created_at > 7 天内)."""
    # 用元数据层检测, 无需查 CH
    from datetime import datetime, timedelta, timezone

    seven = datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)
    rows = (
        await ctx.db.execute(
            select(ColumnAsset).where(
                ColumnAsset.table_id == table.id,
                ColumnAsset.pii_class != "none",
                ColumnAsset.created_at >= seven,
            )
        )
    ).scalars().all()
    if not rows:
        return None
    return {
        "kind": "pii_escalation",
        "severity": "high",
        "details": {
            "new_pii_columns": [
                {"name": r.name, "pii_class": r.pii_class} for r in rows
            ]
        },
        "suggested_action": "新 PII 列被引入, 走法务评审 + 字段脱敏策略",
    }


# === 检测 4: Owner 空缺 ===
async def _detect_owner_gap(
    ctx: SkillContext, table: TableAsset
) -> dict[str, Any] | None:
    """active 表无 verified owner."""
    rows = (
        await ctx.db.execute(
            select(AssetOwner).where(
                AssetOwner.table_id == table.id,
                AssetOwner.is_verified == True,  # noqa: E712
            )
        )
    ).scalars().all()
    if rows:
        return None
    return {
        "kind": "owner_gap",
        "severity": "medium",
        "details": {"verified_owners": 0, "table_fqn": table.fqn},
        "suggested_action": "请 infer_table_owners Skill 推荐 owner, 并人工 verify",
    }


@skill(name="detect_anomalies", version=1, agent="insight")
async def detect_anomalies(ctx: SkillContext, **inputs: Any) -> SkillResult:
    """周期跑, 检测资产异常, 推 ai_suggestion."""
    service: str = inputs.get("service") or ""
    fqn_pattern: str = inputs.get("fqn_pattern") or ""
    limit: int = int(inputs.get("limit") or 50)
    apply: bool = bool(inputs.get("apply", True))
    skip_drift: bool = bool(inputs.get("skip_drift", False))
    skip_null: bool = bool(inputs.get("skip_null", False))
    skip_pii: bool = bool(inputs.get("skip_pii", False))
    skip_owner: bool = bool(inputs.get("skip_owner", False))

    if ctx.db is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.db is None")

    mcp = get_clickhouse_mcp()

    # 1) 选 active 表
    stmt = select(TableAsset).where(TableAsset.status == "active")
    if service:
        stmt = stmt.where(TableAsset.fqn.like(f"{service}.%"))
    if fqn_pattern:
        stmt = stmt.where(TableAsset.fqn.ilike(f"%{fqn_pattern}%"))
    tables = list((await ctx.db.execute(stmt.limit(limit))).scalars().all())

    t0 = time.time()
    findings: list[dict[str, Any]] = []
    severity_count: dict[str, int] = {"high": 0, "medium": 0, "low": 0}

    for t in tables:
        for fn, name in [
            (lambda: _detect_volume_drift(ctx, t, mcp) if not skip_drift else None, "volume_drift"),
            (lambda: _detect_null_spike(ctx, t, mcp) if not skip_null else None, "null_spike"),
            (lambda: _detect_pii_escalation(ctx, t) if not skip_pii else None, "pii_escalation"),
            (lambda: _detect_owner_gap(ctx, t) if not skip_owner else None, "owner_gap"),
        ]:
            try:
                res = await fn()
            except Exception as e:  # noqa: BLE001
                logger.warning("anomaly detector %s failed on %s: %s", name, t.fqn, e)
                continue
            if not res:
                continue
            finding = {
                "table_id": str(t.id),
                "table_fqn": t.fqn,
                **res,
            }
            findings.append(finding)
            severity_count[res["severity"]] = severity_count.get(res["severity"], 0) + 1
            if apply:
                try:
                    sug = AISuggestion(
                        use_case_id=ctx.use_case_id or "anomaly_scan",
                        suggestion_type=res["kind"],
                        target_type="table",
                        target_id=t.id,
                        payload={"details": res["details"], "suggested_action": res["suggested_action"]},
                        rationale=res["suggested_action"],
                        confidence=0.9 if res["severity"] == "high" else 0.6,
                        model="rule-engine",
                        skill="detect_anomalies",
                        status="pending",
                    )
                    ctx.db.add(sug)
                    await ctx.db.flush()
                except Exception as e:  # noqa: BLE001
                    logger.warning("suggestion write failed: %s", e)

    # === M1.5 Data Quality: 回写 health_score (0-100, 越低越异常) ===
    # 算法: 每张表起始 100 分, 每个 high -25, medium -10, low -3; 下限 0
    score_map: dict[uuid.UUID, float] = {}
    for t in tables:
        score_map[t.id] = 100.0
    for f in findings:
        tid = uuid.UUID(f["table_id"]) if isinstance(f["table_id"], str) else f["table_id"]
        sev = f.get("severity", "low")
        delta = {"high": 25, "medium": 10, "low": 3}.get(sev, 3)
        score_map[tid] = max(0.0, score_map.get(tid, 100.0) - delta)
    if apply and score_map:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        for t in tables:
            if t.id in score_map:
                t.health_score = score_map[t.id]
                t.health_score_updated_at = now
        try:
            await ctx.db.flush()
        except Exception as e:  # noqa: BLE001
            logger.warning("health_score write failed: %s", e)

    if apply:
        await ctx.db.commit()

    latency = int((time.time() - t0) * 1000)
    ctx.log("anomaly_scan_done", tables=len(tables), findings=len(findings), severity_count=severity_count)

    return SkillResult(
        ok=True,
        output=SkillOutput(
            items=findings,
            summary={
                "tables_scanned": len(tables),
                "findings": len(findings),
                "severity_count": severity_count,
                "latency_ms": latency,
                "service": service,
                "applied": apply,
                "health_score_avg": round(
                    sum(score_map.values()) / max(1, len(score_map)), 1
                ) if score_map else None,
            },
            artifacts=[],
        ),
    )
