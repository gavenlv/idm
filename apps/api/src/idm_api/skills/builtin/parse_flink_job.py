"""parse_flink_job: 从 GitHub 读 Flink SQL / JAR 清单 → 解析 source / sink / transform → 写 table_lineage.

适用 6 阶段真实管道阶段 1 (Flink preprocess) / 5 (Flink load to ClickHouse).

Inputs:
    repo: str          # 例如 "company/flink-jobs"
    paths: list[str]   # 例如 ["jobs/orders_*.sql"]
    ref: str = "HEAD"  # branch / commit
    apply: bool = True # 直接写 lineage vs ai_suggestion
    stage: int = None  # 6 阶段管道标号 1|5 (强约束)
    transform_subtype: str = "preprocess"  # preprocess | load_ch (语义区分)
    pipeline_name: str = None  # 写入 pipelines 表 (type='flink_job', stage=stage)

Outputs (SkillOutput.items):
    [{upstream_fqn, downstream_fqn, transform_type, transform_subtype, sql, stage, confidence, source}, ...]

写入:
    pipelines (type='flink_job', stage=stage) — 可选
    table_lineage (transform_type='flink_sql', transform_subtype=transform_subtype, pipeline_stage=stage, source='github_mcp')
"""
from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID, uuid4

import sqlglot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.skills.mcp import get_github_mcp
from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill
from idm_kg.models.ai_suggestion import AISuggestion
from idm_kg.models.database import Database
from idm_kg.models.pipeline import Pipeline
from idm_kg.models.schema import Schema
from idm_kg.models.service import Service
from idm_kg.models.table_asset import TableAsset
from idm_kg.models.table_lineage import TableLineage

logger = logging.getLogger(__name__)


def _parse_github_repo(repo: str) -> tuple[str, str]:
    """'company/flink-jobs' -> (company, flink-jobs)."""
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return owner.strip(), name.strip()
    return "", repo.strip()


def _validate_stage(stage: int | None) -> int | None:
    """6 阶段管道标号 1|2|3|4|5|6; Flink Job 只可能在 1 (preprocess) 或 5 (load) 阶段."""
    if stage is None:
        return None
    stage = int(stage)
    if stage not in (1, 5):
        raise ValueError(f"stage must be 1|5 for Flink job, got {stage}")
    return stage


def _validate_transform_subtype(subtype: str | None) -> str:
    """Flink 只能为 preprocess (阶段 1) 或 load_ch (阶段 5)."""
    subtype = (subtype or "preprocess").lower()
    if subtype not in ("preprocess", "load_ch"):
        raise ValueError(f"transform_subtype must be preprocess|load_ch, got {subtype}")
    return subtype


def _extract_table_refs_from_sql(sql: str) -> tuple[set[str], set[str]]:
    """用 sqlglot 解析 Flink SQL, 提取 source table 和 sink table (INSERT/CREATE).

    返回: (sources: set[fqn], sinks: set[fqn])
    """
    sources: set[str] = set()
    sinks: set[str] = set()
    for stmt in sqlglot.parse(sql, read="spark"):
        if stmt is None:
            continue
        # INSERT INTO sink SELECT ... FROM source
        if isinstance(stmt, sqlglot.exp.Insert):
            target = stmt.this
            if hasattr(target, "name"):
                parts = []
                db = target.args.get("db")
                if db is not None:
                    parts.append(str(db))
                parts.append(str(target.name))
                sinks.add(".".join(parts).lower())

            for tbl in stmt.find_all(sqlglot.exp.Table):
                parts = []
                db = tbl.args.get("db")
                if db is not None:
                    parts.append(str(db))
                parts.append(str(tbl.name))
                sources.add(".".join(parts).lower())
        # CREATE TABLE sink AS SELECT ... FROM source
        elif isinstance(stmt, sqlglot.exp.Create):
            if stmt.kind in ("TABLE", "VIEW"):
                target = stmt.this
                if hasattr(target, "name") and target.name:
                    parts = []
                    db = target.args.get("db")
                    if db is not None:
                        parts.append(str(db))
                    parts.append(str(target.name))
                    sinks.add(".".join(parts).lower())
                # 从 AS SELECT 找 source
                if stmt.expression is not None:
                    for tbl in stmt.expression.find_all(sqlglot.exp.Table):
                        parts = []
                        db = tbl.args.get("db")
                        if db is not None:
                            parts.append(str(db))
                        parts.append(str(tbl.name))
                        sources.add(".".join(parts).lower())
    return sources, sinks


async def _ensure_flink_namespace(db: AsyncSession) -> UUID:
    """为 Flink 引用表准备 service -> database -> schema (idempotent).

    service='flink' -> database='flink_jobs' -> schema='default'
    """
    stmt = select(Service).where(Service.name == "flink")
    svc = (await db.execute(stmt)).scalar_one_or_none()
    if svc is None:
        svc = Service(id=uuid4(), name="flink", type="flink",
                      description="Flink 任务引用的逻辑表")
        db.add(svc)
        await db.flush()

    stmt = select(Database).where(Database.service_id == svc.id, Database.name == "flink_jobs")
    db_obj = (await db.execute(stmt)).scalar_one_or_none()
    if db_obj is None:
        db_obj = Database(id=uuid4(), service_id=svc.id, name="flink_jobs",
                          description="Flink 任务引用表集合")
        db.add(db_obj)
        await db.flush()

    stmt = select(Schema).where(Schema.database_id == db_obj.id, Schema.name == "default")
    sch = (await db.execute(stmt)).scalar_one_or_none()
    if sch is None:
        sch = Schema(id=uuid4(), database_id=db_obj.id, name="default",
                     description="Flink 引用表默认 schema")
        db.add(sch)
        await db.flush()
    return sch.id


async def _upsert_table_asset(
    db: AsyncSession, fqn: str, name: str, sch_id: UUID | None
) -> UUID | None:
    """为 source / sink 表创建/获取 table_asset (asset_subtype='flink_table')."""
    stmt = select(TableAsset).where(TableAsset.fqn == fqn)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is not None:
        return row.id
    if sch_id is None:
        return None
    row = TableAsset(
        id=uuid4(),
        schema_id=sch_id,
        name=name,
        fqn=fqn,
        asset_type="table",
        asset_subtype="flink_table",
        external_ref=f"flink://{fqn}",
        tier="normal",
        status="active",
        description=f"Flink 引用表: {fqn}",
        description_source="flink_sql_parse",
        column_count=0,
        extra={"detected_from": "parse_flink_job"},
    )
    db.add(row)
    await db.flush()
    return row.id


async def _upsert_pipeline(
    db: AsyncSession, name: str, transform_subtype: str, stage: int | None, source_url: str
) -> UUID:
    """upsert pipelines (type='flink_job', stage=stage)."""
    stmt = select(Pipeline).where(Pipeline.name == name, Pipeline.type == "flink_job")
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is not None:
        if stage is not None:
            row.stage = stage
        row.config = {**(row.config or {}), "transform_subtype": transform_subtype, "source_url": source_url}
        return row.id
    row = Pipeline(
        id=uuid4(),
        name=name,
        type="flink_job",
        stage=stage,
        source_code_url=source_url,
        description=f"Flink job {name} (transform_subtype={transform_subtype})",
        config={"transform_subtype": transform_subtype, "source_url": source_url},
    )
    db.add(row)
    await db.flush()
    return row.id


@skill(name="parse_flink_job", version=2, agent="lineage")
async def parse_flink_job(ctx: SkillContext, **inputs: Any) -> SkillResult:
    repo: str = inputs.get("repo") or ""
    paths: list[str] = inputs.get("paths") or []
    ref: str = inputs.get("ref") or "HEAD"
    apply: bool = bool(inputs.get("apply", True))
    # === 6 阶段管道 (2026-06-08 M1.5 强化) ===
    try:
        stage = _validate_stage(inputs.get("stage"))
    except ValueError as e:
        return SkillResult(ok=False, output=SkillOutput(), error=str(e))
    try:
        transform_subtype = _validate_transform_subtype(inputs.get("transform_subtype"))
    except ValueError as e:
        return SkillResult(ok=False, output=SkillOutput(), error=str(e))
    pipeline_name: str | None = inputs.get("pipeline_name")
    # 自动一致性: stage=1 → transform_subtype=preprocess; stage=5 → transform_subtype=load_ch
    if stage == 1 and transform_subtype != "preprocess":
        transform_subtype = "preprocess"
    if stage == 5 and transform_subtype != "load_ch":
        transform_subtype = "load_ch"

    if not repo:
        return SkillResult(ok=False, output=SkillOutput(), error="repo is required")
    if not paths:
        return SkillResult(ok=False, output=SkillOutput(), error="paths is required")
    if ctx.db is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.db is None")

    owner, repo_name = _parse_github_repo(repo)
    if not owner or not repo_name:
        return SkillResult(ok=False, output=SkillOutput(), error=f"invalid repo: {repo}")

    gh = get_github_mcp()
    use_local = gh._use_local  # M1.5 演示: 本地 fixture 模式
    if not gh.has_token and not use_local:
        return SkillResult(
            ok=False,
            output=SkillOutput(),
            error="MCP_GITHUB_TOKEN not set and MOCK_GITHUB_ROOT is empty",
        )

    # 1) 列文件
    all_files: list[str] = []
    for p in paths:
        try:
            if use_local:
                files = await gh.list_files_local(owner=owner, repo=repo_name, ref=ref, path=p)
            else:
                files = await gh.list_files(owner=owner, repo=repo_name, ref=ref, path=p)
            all_files.extend([f["path"] for f in files if f.get("type") == "file"])
        except Exception:  # noqa: BLE001
            pass
    if not all_files:
        return SkillResult(
            ok=True,
            output=SkillOutput(
                items=[],
                summary={"reason": "no files matched", "stage": stage, "transform_subtype": transform_subtype},
            ),
        )

    # 2) 读每个 SQL 文件
    items: list[dict[str, Any]] = []
    skipped = 0
    for path in all_files:
        if not path.lower().endswith((".sql", ".flink.sql")):
            continue
        try:
            if use_local:
                sql_text = await gh.get_file_local(owner=owner, repo=repo_name, ref=ref, path=path)
            else:
                sql_text = await gh.get_file(owner=owner, repo=repo_name, ref=ref, path=path)
        except Exception:  # noqa: BLE001
            skipped += 1
            continue

        # 3) sqlglot 解析
        sources, sinks = _extract_table_refs_from_sql(sql_text)
        if not sources and not sinks:
            skipped += 1
            continue

        # 3.5) 写 pipeline (如提供 pipeline_name)
        if pipeline_name:
            source_url = f"github://{owner}/{repo_name}/{path}"
            await _upsert_pipeline(
                db=ctx.db,
                name=pipeline_name,
                transform_subtype=transform_subtype,
                stage=stage,
                source_url=source_url,
            )

        # 3.6) ensure flink namespace + upsert source/sink table_assets
        #  (没有这些 asset 就没法写血缘, 即使 SQL 解析对了)
        flink_sch_id = await _ensure_flink_namespace(ctx.db)
        for fqn in list(sources) + list(sinks):
            await _upsert_table_asset(ctx.db, fqn=fqn, name=fqn.split(".")[-1], sch_id=flink_sch_id)

        # 4) 写血缘边
        for sink in sinks:
            for src in sources:
                if apply:
                    lineage = TableLineage(
                        id=uuid4(),
                        upstream_id=UUID(int=0),  # placeholder, replaced by lookup
                        downstream_id=UUID(int=0),
                        transform_type="flink_sql",
                        transform_subtype=transform_subtype,
                        pipeline_stage=stage,
                        sql=sql_text[:8000],
                        confidence=0.9,
                        source="github_mcp",
                        job_id=f"github://{owner}/{repo_name}/{path}",
                        extra={"flink_sql_file": path, "ref": ref, "transform_subtype": transform_subtype},
                    )
                    # 简单做法: 用 fqn 查 table_asset
                    up_stmt = select(TableAsset).where(TableAsset.fqn == src)
                    down_stmt = select(TableAsset).where(TableAsset.fqn == sink)
                    up_row = (await ctx.db.execute(up_stmt)).scalar_one_or_none()
                    down_row = (await ctx.db.execute(down_stmt)).scalar_one_or_none()
                    if up_row is not None and down_row is not None:
                        lineage.upstream_id = up_row.id
                        lineage.downstream_id = down_row.id
                        ctx.db.add(lineage)
                        items.append(
                            {
                                "upstream_fqn": src,
                                "downstream_fqn": sink,
                                "transform_type": "flink_sql",
                                "transform_subtype": transform_subtype,
                                "pipeline_stage": stage,
                                "source": "github_mcp",
                                "flink_file": path,
                            }
                        )
                else:
                    sug = AISuggestion(
                        suggestion_type="lineage_inferred",
                        target_type="lineage",
                        target_id=uuid4(),
                        payload={
                            "upstream_fqn": src,
                            "downstream_fqn": sink,
                            "transform_type": "flink_sql",
                            "transform_subtype": transform_subtype,
                            "pipeline_stage": stage,
                            "source": "github_mcp",
                            "flink_file": path,
                            "sql": sql_text[:4000],
                        },
                        rationale=f"Flink SQL 推断: {src} → {sink} (stage={stage}, {transform_subtype})",
                        confidence=0.85,
                        model="github_mcp",
                        skill="parse_flink_job",
                        use_case_id=ctx.use_case_id,
                        status="pending",
                    )
                    ctx.db.add(sug)
                    items.append(
                        {
                            "upstream_fqn": src,
                            "downstream_fqn": sink,
                            "transform_type": "flink_sql",
                            "transform_subtype": transform_subtype,
                            "pipeline_stage": stage,
                            "source": "github_mcp",
                            "flink_file": path,
                            "suggestion_id": str(sug.id),
                        }
                    )

    await ctx.db.commit()

    return SkillResult(
        ok=True,
        output=SkillOutput(
            items=items,
            summary={
                "repo": repo,
                "files_scanned": len(all_files),
                "edges_inferred": len(items),
                "skipped": skipped,
                "apply": apply,
            },
        ),
    )
