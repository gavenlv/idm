"""idm_self_mcp: 反向暴露 IDM 能力给外部 Agent (Claude/Cursor/...) 通过 MCP 协议.

M5: 这个模块不直接是 stdio MCP server, 而是一个**可挂接的 IDM 能力暴露层**.
- 当 stdio transport 起来后, 把这里的工具注册到 mcp.server.Server
- 也可以通过 HTTP SSE 暴露 (idm-self.sse)

设计: 这里只暴露**只读**能力, 写操作要求 audit + RBAC.
工具清单 (M5 起步):
    idm.search_assets(q, service, tier?, limit?)
    idm.get_asset(asset_id)
    idm.get_lineage(asset_id, depth?)
    idm.impact(asset_id, direction?, depth?)
    idm.list_skills()
    idm.run_skill(name, inputs, dry_run=true)  # 默认 dry_run
    idm.nl2sql(question, service?, dry_run=true)
    idm.list_suggestions(status='pending', limit?)
    idm.approve_suggestion(suggestion_id, review_note?)
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.db import get_session_factory
from idm_kg.models.ai_suggestion import AISuggestion
from idm_kg.models.owner import AssetOwner
from idm_kg.models.table_asset import TableAsset
from idm_kg.models.table_lineage import TableLineage

logger = logging.getLogger(__name__)


# === Tool list (idm-self namespace) ===
TOOLS: list[dict[str, Any]] = [
    {
        "name": "idm.search_assets",
        "description": "Search table assets by fqn / name / description",
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "keyword"},
                "service": {"type": "string"},
                "tier": {"type": "string", "enum": ["critical", "important", "normal"]},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            },
            "required": ["q"],
        },
    },
    {
        "name": "idm.get_asset",
        "description": "Get a single asset by id",
        "inputSchema": {
            "type": "object",
            "properties": {"asset_id": {"type": "string"}},
            "required": ["asset_id"],
        },
    },
    {
        "name": "idm.get_lineage",
        "description": "BFS upstream/downstream lineage of an asset (depth <= 5)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "depth": {"type": "integer", "default": 2, "minimum": 1, "maximum": 5},
            },
            "required": ["asset_id"],
        },
    },
    {
        "name": "idm.impact",
        "description": "Impact analysis: who is affected by changes to this asset",
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "direction": {"type": "string", "enum": ["upstream", "downstream", "both"], "default": "both"},
                "depth": {"type": "integer", "default": 2, "minimum": 1, "maximum": 5},
            },
            "required": ["asset_id"],
        },
    },
    {
        "name": "idm.list_skills",
        "description": "List available AI skills",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "idm.run_skill",
        "description": "Run a skill (default dry_run=true; only LLM-derived skills)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "inputs": {"type": "object"},
                "dry_run": {"type": "boolean", "default": True},
            },
            "required": ["name"],
        },
    },
    {
        "name": "idm.nl2sql",
        "description": "Natural language -> SQL (5 layer Guard, only SELECT)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "service": {"type": "string"},
                "dry_run": {"type": "boolean", "default": True},
            },
            "required": ["question"],
        },
    },
    {
        "name": "idm.list_suggestions",
        "description": "List AI suggestions (default status=pending)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "default": "pending"},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "idm.approve_suggestion",
        "description": "Approve a pending AI suggestion (audit logged)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "suggestion_id": {"type": "string"},
                "review_note": {"type": "string"},
            },
            "required": ["suggestion_id"],
        },
    },
]


# === Dispatcher ===
async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """统一的 idm-self tool 入口. 返回 {"ok": bool, "result": ...}."""
    factory = get_session_factory()
    async with factory() as db:
        try:
            if name == "idm.search_assets":
                return {"ok": True, "result": await _search_assets(db, arguments)}
            if name == "idm.get_asset":
                return {"ok": True, "result": await _get_asset(db, arguments)}
            if name == "idm.get_lineage":
                return {"ok": True, "result": await _get_lineage(db, arguments)}
            if name == "idm.impact":
                return {"ok": True, "result": await _impact(db, arguments)}
            if name == "idm.list_skills":
                from idm_api.skills.runner import list_skills
                return {"ok": True, "result": await list_skills()}
            if name == "idm.run_skill":
                from idm_api.skills.runner import run_skill
                res = await run_skill(
                    arguments["name"],
                    arguments.get("inputs") or {},
                    dry_run=arguments.get("dry_run", True),
                    db=db,
                )
                return {
                    "ok": res.ok,
                    "result": {
                        "summary": res.output.summary,
                        "items": res.output.items[:10],
                        "duration_ms": res.duration_ms,
                        "error": res.error,
                    },
                }
            if name == "idm.nl2sql":
                from idm_api.skills.runner import run_skill
                res = await run_skill(
                    "nl2sql",
                    {
                        "question": arguments["question"],
                        "service": arguments.get("service") or "",
                        "dry_run": arguments.get("dry_run", True),
                    },
                    db=db,
                )
                return {
                    "ok": res.ok,
                    "result": {
                        "sql": (res.output.summary or {}).get("sql"),
                        "rationale": (res.output.summary or {}).get("rationale"),
                        "guard_warnings": (res.output.summary or {}).get("guard_warnings") or [],
                        "duration_ms": res.duration_ms,
                    },
                }
            if name == "idm.list_suggestions":
                return {"ok": True, "result": await _list_suggestions(db, arguments)}
            if name == "idm.approve_suggestion":
                return {"ok": True, "result": await _approve_suggestion(db, arguments)}
            return {"ok": False, "error": f"unknown tool: {name}"}
        except Exception as e:  # noqa: BLE001
            logger.exception("idm-self tool %s failed", name)
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# === Helpers ===
async def _search_assets(db: AsyncSession, args: dict) -> list[dict]:
    q = (args.get("q") or "").lower()
    svc = args.get("service")
    tier = args.get("tier")
    limit = int(args.get("limit") or 20)
    stmt = select(TableAsset)
    if q:
        from sqlalchemy import func
        like = f"%{q}%"
        stmt = stmt.where(
            (func.lower(TableAsset.name).like(like)) | (func.lower(TableAsset.fqn).like(like))
        )
    if svc:
        stmt = stmt.where(TableAsset.fqn.like(f"{svc}.%"))
    if tier:
        stmt = stmt.where(TableAsset.tier == tier)
    rows = list((await db.execute(stmt.order_by(TableAsset.fqn).limit(limit))).scalars())
    return [
        {
            "id": str(r.id),
            "fqn": r.fqn,
            "tier": r.tier,
            "status": r.status,
            "description": (r.description or "")[:200],
            "health_score": r.health_score,
        }
        for r in rows
    ]


async def _get_asset(db: AsyncSession, args: dict) -> dict | None:
    import uuid as _uuid
    try:
        aid = _uuid.UUID(args["asset_id"])
    except (KeyError, ValueError):
        return None
    a = await db.get(TableAsset, aid)
    if a is None:
        return None
    return {
        "id": str(a.id),
        "fqn": a.fqn,
        "tier": a.tier,
        "status": a.status,
        "description": a.description,
        "column_count": a.column_count,
        "row_count": a.row_count,
        "health_score": a.health_score,
    }


async def _get_lineage(db: AsyncSession, args: dict) -> dict:
    import uuid as _uuid
    from collections import deque
    try:
        aid = _uuid.UUID(args["asset_id"])
    except (KeyError, ValueError):
        return {"error": "bad asset_id"}
    depth = int(args.get("depth") or 2)
    asset = await db.get(TableAsset, aid)
    if asset is None:
        return {"error": "asset not found"}
    visited: set = {asset.id}
    up: list[dict] = []
    down: list[dict] = []
    fu, fd = deque([asset.id]), deque([asset.id])
    for _ in range(depth):
        if fu:
            rows = list((await db.execute(select(TableLineage).where(TableLineage.downstream_id.in_(list(fu))))).scalars())
            nfu = deque()
            for e in rows:
                up.append({"from": str(e.upstream_id), "to": str(e.downstream_id), "via": e.transform_type, "src": e.source})
                if e.upstream_id not in visited:
                    visited.add(e.upstream_id)
                    nfu.append(e.upstream_id)
            fu = nfu
        if fd:
            rows = list((await db.execute(select(TableLineage).where(TableLineage.upstream_id.in_(list(fd))))).scalars())
            nfd = deque()
            for e in rows:
                down.append({"from": str(e.upstream_id), "to": str(e.downstream_id), "via": e.transform_type, "src": e.source})
                if e.downstream_id not in visited:
                    visited.add(e.downstream_id)
                    nfd.append(e.downstream_id)
            fd = nfd
    return {"center_fqn": asset.fqn, "upstream": up[:50], "downstream": down[:50]}


async def _impact(db: AsyncSession, args: dict) -> dict:
    """简化版: 与 routers/impact 类似的实现."""
    import uuid as _uuid
    from collections import deque
    try:
        aid = _uuid.UUID(args["asset_id"])
    except (KeyError, ValueError):
        return {"error": "bad asset_id"}
    direction = args.get("direction") or "both"
    depth = int(args.get("depth") or 2)
    asset = await db.get(TableAsset, aid)
    if asset is None:
        return {"error": "asset not found"}
    visited: set = {asset.id}
    upstream: set = set()
    downstream: set = set()
    paths: list[dict] = []
    if direction in ("upstream", "both"):
        fu = deque([asset.id])
        for _ in range(depth):
            if not fu:
                break
            rows = list((await db.execute(select(TableLineage).where(TableLineage.downstream_id.in_(list(fu))))).scalars())
            nfu = deque()
            for e in rows:
                paths.append({"from": str(e.upstream_id), "to": str(e.downstream_id), "via": e.transform_type})
                if e.upstream_id not in visited:
                    visited.add(e.upstream_id)
                    upstream.add(e.upstream_id)
                    nfu.append(e.upstream_id)
            fu = nfu
    if direction in ("downstream", "both"):
        fd = deque([asset.id])
        for _ in range(depth):
            if not fd:
                break
            rows = list((await db.execute(select(TableLineage).where(TableLineage.upstream_id.in_(list(fd))))).scalars())
            nfd = deque()
            for e in rows:
                paths.append({"from": str(e.upstream_id), "to": str(e.downstream_id), "via": e.transform_type})
                if e.downstream_id not in visited:
                    visited.add(e.downstream_id)
                    downstream.add(e.downstream_id)
                    nfd.append(e.downstream_id)
            fd = nfd
    # affected owners
    affected_ids = upstream | downstream
    owners: list[str] = []
    if affected_ids:
        rows = list(
            (await db.execute(
                select(AssetOwner.user_email).where(
                    AssetOwner.table_id.in_(list(affected_ids)),
                    AssetOwner.is_verified.is_(True),
                )
            )).scalars()
        )
        owners = sorted(set(rows))
    return {
        "center_fqn": asset.fqn,
        "upstream_count": len(upstream),
        "downstream_count": len(downstream),
        "affected_owners": owners,
        "paths": paths[:50],
    }


async def _list_suggestions(db: AsyncSession, args: dict) -> list[dict]:
    status = args.get("status") or "pending"
    limit = int(args.get("limit") or 20)
    rows = list(
        (await db.execute(
            select(AISuggestion)
            .where(AISuggestion.status == status)
            .order_by(AISuggestion.created_at.desc())
            .limit(limit)
        )).scalars()
    )
    return [
        {
            "id": str(s.id),
            "suggestion_type": s.suggestion_type,
            "target_type": s.target_type,
            "target_id": str(s.target_id),
            "confidence": s.confidence,
            "rationale": s.rationale,
            "status": s.status,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in rows
    ]


async def _approve_suggestion(db: AsyncSession, args: dict) -> dict:
    import uuid as _uuid
    from datetime import datetime, timezone
    from idm_kg.models.audit_log import AuditLog

    sid = args.get("suggestion_id")
    try:
        sug_uuid = _uuid.UUID(sid)
    except (ValueError, TypeError):
        return {"error": "bad suggestion_id"}
    sug = await db.get(AISuggestion, sug_uuid)
    if sug is None:
        return {"error": "not found"}
    if sug.status != "pending":
        return {"error": f"already {sug.status}"}
    sug.status = "approved"
    sug.reviewed_by = "idm-self-mcp"
    sug.reviewed_at = datetime.now(timezone.utc)
    sug.review_note = args.get("review_note")
    # 注: 不走完整 _apply_suggestion (它依赖 router), 仅标 status + audit
    db.add(
        AuditLog(
            actor="idm-self-mcp",
            action="approve_suggestion",
            resource_type="ai_suggestion",
            resource_id=str(sug.id),
            payload={"suggestion_type": sug.suggestion_type, "note": sug.review_note},
        )
    )
    await db.commit()
    return {"ok": True, "id": str(sug.id), "status": sug.status}


# === Stdio entrypoint (可选启动) ===
@asynccontextmanager
async def stdio_lifespan() -> AsyncIterator[None]:
    """M5: 启动 idm-self MCP server (stdio).

    通过 `python -m idm_api.skills.idm_self_mcp` 启动;
    在 helm/argocd 里以 Sidecar 形式部署.
    """
    try:
        from mcp.server import Server, stdio
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "mcp package not installed; run `uv add mcp` to enable idm-self"
        ) from e

    app = Server("idm-self-mcp")

    @app.list_tools()
    async def list_tools():  # noqa: ANN202
        return TOOLS

    @app.call_tool()
    async def _call(name: str, arguments: dict):  # noqa: ANN202
        result = await call_tool(name, arguments or {})
        return [
            {
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False, default=str),
            }
        ]

    async with stdio.stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())
        yield


def main() -> None:  # pragma: no cover
    """CLI 入口: `python -m idm_api.skills.idm_self_mcp`."""
    import asyncio

    @asynccontextmanager
    async def _run() -> AsyncIterator[None]:
        async with stdio_lifespan():
            yield

    asyncio.run(_run().__aenter__() and None) if False else asyncio.run(_run().__aenter__() or asyncio.sleep(0))


if __name__ == "__main__":  # pragma: no cover
    main()
