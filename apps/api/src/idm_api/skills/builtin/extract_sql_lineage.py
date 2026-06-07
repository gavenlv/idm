"""extract_sql_lineage: 用 sqlglot 解析 SQL, 提取表级血缘, 写 table_lineage (source=sqlglot).

Inputs:
    sql: str                  单条 SQL
    downstream_fqn: str       下游表 FQN (service.db.schema.tbl)
    service: str              service 名 (用于在 FQN 未指定时构造上游 FQN)
    database: str             物理库名 (上游默认 db)
    schema: str               物理 schema (上游默认 schema)
    apply: bool               True=写 KG, False=仅返回
    min_confidence: float     sqlglot 解析置信度 (默认 0.9)

Outputs (SkillOutput.items):
    [{upstream_fqn, downstream_fqn, transform_type, confidence, source, sql_excerpt}, ...]

写入:
    table_lineage (transform_type=sql, source=sqlglot, sql=excerpt)

兼容:
    - 优先用 sqlglot; 不可用则降级 regex
    - 上游表 FQN 默认按 <service>.<database>.<schema>.<table> 拼接
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill
from idm_kg.models.table_asset import TableAsset
from idm_kg.models.table_lineage import TableLineage

logger = logging.getLogger(__name__)


def _try_sqlglot(sql: str) -> list[dict[str, Any]] | None:
    """尝试用 sqlglot 提取表血缘; 失败返回 None 走 fallback."""
    try:
        import sqlglot  # type: ignore
    except ImportError:
        return None
    try:
        import sqlglot.optimizer.qualify as qualify  # type: ignore
        from sqlglot.optimizer.lineage import lineage  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    try:
        statements = sqlglot.parse(sql, read="duckdb")
        out: list[dict[str, Any]] = []
        for stmt in statements:
            if stmt is None:
                continue
            # 1) 找目标 (INSERT / CREATE / MERGE)
            target = _extract_target_table(stmt)
            # 2) 找所有引用的源表
            refs: set[str] = set()
            for tbl in stmt.find_all(sqlglot.exp.Table):
                name = tbl.name
                db = tbl.args.get("db") or ""
                sch = tbl.args.get("catalog") or ""
                if name:
                    qual = ".".join([p for p in (sch, db, name) if p])
                    refs.add(qual)
            for r in sorted(refs):
                out.append({"upstream_table": r, "downstream_table": target, "via": "sqlglot"})
        return out if out else []
    except Exception as e:  # noqa: BLE001
        logger.warning("sqlglot parse failed: %s", e)
        return None


def _extract_target_table(stmt) -> str | None:  # type: ignore[no-untyped-def]
    try:
        import sqlglot.expressions as exp  # type: ignore
        # INSERT INTO
        ins = stmt.find(exp.Insert)
        if ins:
            t = ins.this
            if isinstance(t, exp.Schema):
                t = t.this
            if isinstance(t, exp.Table):
                return ".".join(p for p in (t.args.get("catalog"), t.args.get("db"), t.name) if p)
        # CREATE TABLE
        ct = stmt.find(exp.Create)
        if ct:
            t = ct.this
            if isinstance(t, exp.Table):
                return ".".join(p for p in (t.args.get("catalog"), t.args.get("db"), t.name) if p)
    except Exception:  # noqa: BLE001
        return None
    return None


_FALLBACK_TBL_RE = re.compile(
    r"\b(?:from|join|into|update)\s+(?:([a-zA-Z_][\w]*)\.)?(?:([a-zA-Z_][\w]*)\.)?([a-zA-Z_][\w]*)",
    re.IGNORECASE,
)


def _fallback_extract(sql: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in _FALLBACK_TBL_RE.finditer(sql):
        parts = [p for p in (m.group(1), m.group(2), m.group(3)) if p]
        if parts:
            ident = ".".join(parts)
            if ident not in seen:
                seen.add(ident)
                out.append({"upstream_table": ident, "downstream_table": None, "via": "regex"})
    return out


def _build_fqn(service: str, database: str, schema: str, table: str) -> str:
    parts = [p.strip("`\"") for p in (service, database, schema, table) if p]
    return ".".join(parts).lower()


@skill(name="extract_sql_lineage", version=1, agent="lineage")
async def extract_sql_lineage(ctx: SkillContext, **inputs: Any) -> SkillResult:
    sql: str = inputs.get("sql") or ""
    downstream_fqn: str = (inputs.get("downstream_fqn") or "").lower()
    service: str = inputs.get("service") or "unknown"
    database: str = inputs.get("database") or "default"
    schema: str = inputs.get("schema") or "default"
    apply: bool = bool(inputs.get("apply", False))
    min_confidence: float = float(inputs.get("min_confidence") or 0.9)

    if not sql:
        return SkillResult(ok=False, output=SkillOutput(), error="missing required input: 'sql'")
    if ctx.db is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.db is None")

    # 1) 解析
    parsed = _try_sqlglot(sql) or _fallback_extract(sql)
    if not parsed:
        return SkillResult(
            ok=True,
            output=SkillOutput(items=[], summary={"reason": "no tables found", "via": "none"}),
        )
    via = parsed[0].get("via", "unknown")

    # 2) 解析 upstream_table -> upstream_id (TableAsset)
    upstream_ids: list[str] = []
    edges: list[dict[str, Any]] = []
    for e in parsed:
        u = e["upstream_table"]
        u_parts = u.split(".")
        if len(u_parts) == 1:
            uf = _build_fqn(service, database, schema, u_parts[0])
        elif len(u_parts) == 2:
            uf = _build_fqn(service, database, u_parts[0], u_parts[1])
        elif len(u_parts) == 3:
            uf = _build_fqn(service, u_parts[0], u_parts[1], u_parts[2])
        else:
            uf = u.lower()
        row = (
            await ctx.db.execute(select(TableAsset.id).where(TableAsset.fqn == uf))
        ).scalar_one_or_none()
        up_id = str(row) if row else None
        upstream_ids.append(uf)
        edges.append(
            {
                "upstream_fqn": uf,
                "upstream_id": up_id,
                "downstream_fqn": downstream_fqn or (e.get("downstream_table") or "").lower(),
                "via": via,
            }
        )

    # 3) 找 downstream table id (如给了 downstream_fqn)
    downstream_id: str | None = None
    if downstream_fqn:
        row = (
            await ctx.db.execute(select(TableAsset.id).where(TableAsset.fqn == downstream_fqn))
        ).scalar_one_or_none()
        downstream_id = str(row) if row else None

    # 4) 写 KG
    added = 0
    skipped = 0
    if apply and downstream_id:
        for e in edges:
            if not e["upstream_id"]:
                skipped += 1
                continue
            stmt = (
                pg_insert(TableLineage)
                .values(
                    upstream_id=e["upstream_id"],
                    downstream_id=downstream_id,
                    transform_type="sql",
                    sql=sql[:2048],
                    confidence=0.95 if via == "sqlglot" else 0.6,
                    source="sqlglot" if via == "sqlglot" else "regex",
                    extra={"via": via, "excerpt": sql[:200]},
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        TableLineage.upstream_id,
                        TableLineage.downstream_id,
                        TableLineage.transform_type,
                    ]
                )
            )
            await ctx.db.execute(stmt)
            added += 1
        await ctx.db.commit()

    summary = {
        "sql_len": len(sql),
        "via": via,
        "upstream_count": len(edges),
        "downstream_fqn": downstream_fqn or None,
        "downstream_id_resolved": downstream_id is not None,
        "edges_added": added,
        "edges_skipped_no_upstream": skipped,
        "apply": apply,
    }
    return SkillResult(
        ok=True,
        output=SkillOutput(
            items=edges,
            summary=summary,
            artifacts=[downstream_id] if downstream_id else [],
        ),
    )
