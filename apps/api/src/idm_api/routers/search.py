"""/api/v1/search: 全局搜索 (assets / owners / tags / glossary / use cases / suggestions).

返回 6 类统一格式的 SearchHit 列表, 供前端 Cmd+K 调色板使用.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.config import Settings, get_settings
from idm_api.db import get_db
from idm_api.schemas import SearchHit, SearchResponse
from idm_kg.models.ai_suggestion import AISuggestion
from idm_kg.models.glossary import GlossaryTerm
from idm_kg.models.owner import AssetOwner
from idm_kg.models.table_asset import TableAsset
from idm_kg.models.tag import Tag

router = APIRouter()


def _score(text: str, q: str) -> float:
    """简单 score: 完全包含=1.0, 前缀=0.9, 子串=0.6, 否则 0."""
    if not text or not q:
        return 0.0
    t = text.lower()
    n = q.lower()
    if t == n:
        return 1.0
    if t.startswith(n):
        return 0.9
    if n in t:
        return 0.6
    return 0.0


async def _search_assets(db: AsyncSession, q: str, limit: int) -> list[SearchHit]:
    stmt = select(TableAsset).where(
        or_(
            TableAsset.fqn.ilike(f"%{q}%"),
            TableAsset.name.ilike(f"%{q}%"),
            TableAsset.description.ilike(f"%{q}%"),
        )
    ).order_by(TableAsset.fqn).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        SearchHit(
            kind="asset",
            id=str(r.id),
            title=r.fqn,
            subtitle=f"{r.asset_type} · tier={r.tier} · cols={r.column_count}",
            url=f"/?q={r.fqn}",
            score=_score(r.fqn, q),
            extra={"tier": r.tier, "asset_type": r.asset_type},
        )
        for r in rows
    ]


async def _search_owners(db: AsyncSession, q: str, limit: int) -> list[SearchHit]:
    stmt = select(AssetOwner).where(
        or_(
            AssetOwner.user_email.ilike(f"%{q}%"),
            AssetOwner.user_name.ilike(f"%{q}%"),
            AssetOwner.team.ilike(f"%{q}%"),
        )
    ).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        SearchHit(
            kind="owner",
            id=str(r.id),
            title=r.user_name or r.user_email,
            subtitle=f"{r.role} · {r.team or '—'} · {r.table_id}",
            url="/owners",
            score=_score(r.user_email, q),
            extra={"is_verified": r.is_verified},
        )
        for r in rows
    ]


async def _search_tags(db: AsyncSession, q: str, limit: int) -> list[SearchHit]:
    stmt = select(Tag).where(
        or_(Tag.name.ilike(f"%{q}%"), (Tag.description or "").ilike(f"%{q}%"))
    ).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        SearchHit(
            kind="tag",
            id=str(r.id),
            title=r.name,
            subtitle=f"{r.category} · {r.color}",
            url="/tags",
            score=_score(r.name, q),
            extra={"color": r.color, "category": r.category},
        )
        for r in rows
    ]


async def _search_glossary(db: AsyncSession, q: str, limit: int) -> list[SearchHit]:
    stmt = select(GlossaryTerm).where(
        or_(
            GlossaryTerm.name.ilike(f"%{q}%"),
            GlossaryTerm.definition.ilike(f"%{q}%"),
        )
    ).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        SearchHit(
            kind="glossary",
            id=str(r.id),
            title=r.name,
            subtitle=r.definition[:120] + ("…" if len(r.definition) > 120 else ""),
            url="/glossary",
            score=_score(r.name, q),
            extra={"domain": r.domain, "synonyms": list(r.synonyms or [])},
        )
        for r in rows
    ]


async def _search_suggestions(db: AsyncSession, q: str, limit: int) -> list[SearchHit]:
    stmt = select(AISuggestion).where(
        or_(
            AISuggestion.skill.ilike(f"%{q}%"),
            AISuggestion.suggestion_type.ilike(f"%{q}%"),
            AISuggestion.target_id.cast(__import__("sqlalchemy").String).ilike(f"%{q}%"),
        )
    ).order_by(AISuggestion.created_at.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        SearchHit(
            kind="suggestion",
            id=str(r.id),
            title=f"{r.suggestion_type} ({r.skill})",
            subtitle=f"target={r.target_id} · status={r.status}",
            url="/suggestions",
            score=_score(r.skill + " " + r.suggestion_type, q),
        )
        for r in rows
    ]


def _search_use_cases(q: str, settings: Settings, limit: int) -> list[SearchHit]:
    base = os.environ.get("IDM_USE_CASES_DIR")
    if not base:
        base = str(Path(__file__).resolve().parents[5] / "use_cases")
    p = Path(base)
    if not p.exists():
        return []
    hits: list[SearchHit] = []
    n = q.lower()
    for f in sorted(p.glob("*.yml")):
        try:
            spec: dict[str, Any] = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        text = " ".join(
            [
                str(spec.get("id") or f.stem),
                str(spec.get("description") or ""),
                " ".join(spec.get("owners") or []),
            ]
        )
        s = _score(text, q)
        if s == 0 and n:
            # 模糊子串
            if n in text.lower():
                s = 0.5
        if s > 0:
            hits.append(
                SearchHit(
                    kind="use_case",
                    id=str(spec.get("id") or f.stem),
                    title=str(spec.get("id") or f.stem),
                    subtitle=str(spec.get("description") or ""),
                    url=f"/use-cases/{spec.get('id') or f.stem}",
                    score=s,
                )
            )
    hits.sort(key=lambda x: x.score, reverse=True)
    return hits[:limit]


@router.get("", response_model=SearchResponse, summary="Global search")
async def global_search(
    q: str = Query(..., min_length=1, max_length=128, description="搜索词"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SearchResponse:
    """按 q 检索 6 类实体, 合并按 score 排序."""
    per = max(3, limit // 4)
    hits: list[SearchHit] = []
    hits.extend(await _search_assets(db, q, per))
    hits.extend(await _search_owners(db, q, per))
    hits.extend(await _search_tags(db, q, per))
    hits.extend(await _search_glossary(db, q, per))
    hits.extend(await _search_suggestions(db, q, per))
    hits.extend(_search_use_cases(q, settings, per))
    hits.sort(key=lambda x: x.score, reverse=True)
    return SearchResponse(query=q, total=len(hits), items=hits[:limit])
