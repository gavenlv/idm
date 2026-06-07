"""infer_table_owners: 推断表的 Owner / Steward, 写入 asset_owners (待人工 verify).

输入线索 (按优先级, 命中即信):
  1) dbt meta.owner / meta.team (来源: dbt_manifest 已 parse)
  2) Airflow DAG default_args.owner / params.team (来源: 后续 airflow skill, 暂用 mock)
  3) git blame 最近 5 个 commit 作者 (来源: github MCP, 暂跳过)
  4) LLM 推断 (fallback): 基于 table.fqn / description / 关联 dbt model 路径

Inputs:
    service: str                 仅扫描某 service 下的表 (空 = 全部)
    fqn_pattern: str             模糊匹配 table.fqn
    min_confidence: float        低于此值不入
    llm_threshold: float         高于此值才信 LLM, 更高优先信 dbt/airflow (默认 0.8)
    apply: bool                  True=直接写入 asset_owners (is_verified=False);
                                False=写 ai_suggestion (status=pending) 供人工确认

Outputs (SkillOutput.items):
    [{table_id, table_fqn, role, user_email, user_name, team,
      source, confidence, suggestion_id|owner_id}, ...]

写入:
    asset_owners (apply=True)  或  ai_suggestion suggestion_type=owner
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill
from idm_kg.models.ai_suggestion import AISuggestion
from idm_kg.models.owner import AssetOwner
from idm_kg.models.table_asset import TableAsset

logger = logging.getLogger(__name__)

VALID_ROLES = {"owner", "steward", "consumer"}


def _team_from_path(fqn: str) -> str:
    """从 FQN 推断业务团队 (命名约定: team_domain__layer).
    fct_/dim_/stg_ 前缀归到对应的 domain (shop/finance/growth/...)."""
    if not fqn:
        return "unknown"
    parts = fqn.split(".")
    if len(parts) >= 1:
        # 优先 service 段 (clickhouse-prod / dbt-shop_dw ...)
        svc = parts[0]
        for kw, team in [
            ("shop", "data-shop"),
            ("finance", "data-finance"),
            ("growth", "data-growth"),
            ("logistics", "data-logistics"),
            ("superset", "bi-team"),
        ]:
            if kw in svc.lower():
                return team
    # 退化: 路径层归类
    for layer_prefix, team in [
        ("fct_", "data-engineering"),
        ("dim_", "data-engineering"),
        ("stg_", "data-engineering"),
        ("ods_", "data-platform"),
    ]:
        if any(layer_prefix in p for p in parts):
            return team
    return "data-platform"


def _heuristic_owner(t: TableAsset) -> dict[str, Any] | None:
    """纯规则: 路径前缀 + 服务名 → 团队 / 默认 owner 邮箱."""
    team = _team_from_path(t.fqn)
    # 团队 → 默认 owner 邮箱 (按公司目录约定)
    mapping = {
        "data-shop": ("shop-data@company.com", "Shop Data Team"),
        "data-finance": ("fin-data@company.com", "Finance Data Team"),
        "data-growth": ("growth-data@company.com", "Growth Data Team"),
        "data-logistics": ("logi-data@company.com", "Logistics Data Team"),
        "data-engineering": ("dwh-eng@company.com", "DWH Engineering"),
        "data-platform": ("platform-data@company.com", "Data Platform"),
        "bi-team": ("bi-team@company.com", "BI & Analytics"),
        "unknown": ("data-steward@company.com", "Data Steward"),
    }
    email, name = mapping.get(team, mapping["unknown"])
    return {
        "user_email": email,
        "user_name": name,
        "team": team,
        "role": "owner",
        "source": "fqn_inference",
        "confidence": 0.6,
    }


def _llm_owner(t: TableAsset) -> dict[str, Any] | None:
    """LLM 推断: 同步阻塞式, 失败返回 None.
    实际通过 ctx.llm.complete 调用, 不在这里做."""
    return None


async def _maybe_llm(ctx: SkillContext, t: TableAsset) -> dict[str, Any] | None:
    if not ctx.llm:
        return None
    prompt = (
            "你是数据治理专家。基于以下表的元信息(无敏感样本), 推断该表的 owner 团队和负责人邮箱。\n"
            "只输出严格 JSON, 不要任何解释。\n\n"
            "JSON schema:\n"
            '{"user_email": "team@company.com", "user_name": "Team Name", '
            '"team": "team-slug", "role": "owner|steward|consumer", "confidence": 0.0-1.0, '
            '"reasoning": "<20-40 字中文>"}\n\n'
            f"FQN: {t.fqn}\n"
            f"Type: {t.asset_type}\n"
            f"Tier: {t.tier}\n"
            f"Description: {t.description or '(无)'}\n"
        )
    try:
        resp = await ctx.llm.complete(
            [
                {"role": "system", "content": "输出严格 JSON, 不含任何解释。"},
                {"role": "user", "content": prompt},
            ],
            profile="default",
            temperature=0.1,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        import json as _json

        text = resp["content"].strip()
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            data = _json.loads(text[start : end + 1])
        else:
            return None
        role = str(data.get("role") or "owner").lower()
        if role not in VALID_ROLES:
            role = "owner"
        return {
            "user_email": (data.get("user_email") or "").strip().lower(),
            "user_name": (data.get("user_name") or "").strip(),
            "team": (data.get("team") or "data-platform").strip().lower(),
            "role": role,
            "source": "ai_inferred",
            "confidence": float(data.get("confidence") or 0.5),
            "reasoning": (data.get("reasoning") or "").strip()[:300],
            "model": resp["model"],
        }
    except Exception as e:  # noqa: BLE001
        ctx.log("llm_owner_failed", fqn=t.fqn, error=str(e)[:120])
        return None


@skill(name="infer_table_owners", version=1, agent="owner")
async def infer_table_owners(ctx: SkillContext, **inputs: Any) -> SkillResult:
    service: str = inputs.get("service") or ""
    fqn_pattern: str = inputs.get("fqn_pattern") or ""
    min_confidence: float = float(inputs.get("min_confidence") or 0.55)
    llm_threshold: float = float(inputs.get("llm_threshold") or 0.8)
    apply: bool = bool(inputs.get("apply", False))

    if ctx.db is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.db is None")

    # 1) 拉候选表 (service 走 fqn 前缀)
    stmt = select(TableAsset)
    if service:
        stmt = stmt.where(TableAsset.fqn.like(f"{service}.%"))
    if fqn_pattern:
        stmt = stmt.where(TableAsset.fqn.ilike(f"%{fqn_pattern}%"))
    tables = list((await ctx.db.execute(stmt.limit(200))).scalars().all())
    ctx.log("tables_selected", count=len(tables))

    if not tables:
        return SkillResult(ok=True, output=SkillOutput(items=[], summary={"reason": "no tables"}))

    items: list[dict[str, Any]] = []
    skipped_low = 0
    skipped_dup = 0
    llm_calls = 0

    for t in tables:
        # 2) 已有 owner + is_verified=True, 跳过
        existing = (
            await ctx.db.execute(
                select(AssetOwner).where(AssetOwner.table_id == t.id, AssetOwner.is_verified.is_(True))
            )
        ).scalars().all()
        if existing:
            skipped_dup += 1
            continue

        # 3) 启发式推断
        inf = _heuristic_owner(t)
        source = "fqn_inference"
        confidence = inf["confidence"] if inf else 0.0
        reasoning = ""

        # 4) 启发式信心不足时, 调用 LLM
        if confidence < llm_threshold and ctx.llm:
            llm_calls += 1
            llm_inf = await _maybe_llm(ctx, t)
            if llm_inf and llm_inf.get("user_email"):
                inf = llm_inf
                source = "ai_inferred"
                confidence = llm_inf["confidence"]
                reasoning = llm_inf.get("reasoning", "")

        if not inf or not inf.get("user_email"):
            skipped_low += 1
            continue
        if confidence < min_confidence:
            skipped_low += 1
            continue

        # 5) 写库
        if apply:
            stmt_ins = (
                pg_insert(AssetOwner)
                .values(
                    table_id=t.id,
                    user_email=inf["user_email"],
                    user_name=inf.get("user_name"),
                    team=inf.get("team"),
                    role=inf.get("role", "owner"),
                    source=source,
                    confidence=confidence,
                    is_verified=False,
                )
                .on_conflict_do_nothing(
                    index_elements=[AssetOwner.table_id, AssetOwner.user_email, AssetOwner.role]
                )
            )
            await ctx.db.execute(stmt_ins)
            # 读回 id
            row = (
                await ctx.db.execute(
                    select(AssetOwner).where(
                        AssetOwner.table_id == t.id,
                        AssetOwner.user_email == inf["user_email"],
                        AssetOwner.role == inf.get("role", "owner"),
                    )
                )
            ).scalar_one_or_none()
            owner_id = str(row.id) if row else None
            sug_id = None
        else:
            sug = AISuggestion(
                suggestion_type="owner",
                target_type="table",
                target_id=t.id,
                payload={
                    "user_email": inf["user_email"],
                    "user_name": inf.get("user_name"),
                    "team": inf.get("team"),
                    "role": inf.get("role", "owner"),
                    "table_fqn": t.fqn,
                },
                rationale=reasoning or f"{source} conf={confidence}",
                confidence=confidence,
                model=inf.get("model", "n/a"),
                skill="infer_table_owners",
                use_case_id=ctx.use_case_id,
                prompt_hash=None,
                langfuse_trace_id=None,
                status="pending",
            )
            ctx.db.add(sug)
            await ctx.db.flush()
            owner_id = None
            sug_id = str(sug.id)

        items.append(
            {
                "table_id": str(t.id),
                "table_fqn": t.fqn,
                "role": inf.get("role", "owner"),
                "user_email": inf["user_email"],
                "user_name": inf.get("user_name"),
                "team": inf.get("team"),
                "source": source,
                "confidence": confidence,
                "owner_id": owner_id,
                "suggestion_id": sug_id,
            }
        )
        ctx.log("owner_inferred", fqn=t.fqn, email=inf["user_email"], conf=confidence)

    await ctx.db.commit()

    summary = {
        "tables_scanned": len(tables),
        "owners_inferred": len(items),
        "skipped_already_verified": skipped_dup,
        "skipped_low_confidence": skipped_low,
        "llm_calls": llm_calls,
        "apply": apply,
        "by_team": _count_by(items, "team"),
        "by_source": _count_by(items, "source"),
    }
    return SkillResult(
        ok=True,
        output=SkillOutput(
            items=items,
            summary=summary,
            artifacts=[(i.get("owner_id") or i.get("suggestion_id") or "") for i in items],
        ),
    )


def _count_by(items: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        v = it.get(key) or "unknown"
        out[v] = out.get(v, 0) + 1
    return out
