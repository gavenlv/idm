"""parse_mex_io: 从 GitHub 读 MEX (Model Execution) io.yaml / README → 黑盒声明解析 → 写 pipeline + 血缘.

MEX 是真实管道阶段 3 的"黑盒"模型推理服务, 业务侧只暴露 IO 声明, 没有源码可读。
本 skill 从 io.yaml 中提取 input/output FQN, 写入:
  - pipelines 表 (type='mex_model', stage=3)
  - table_lineage (transform_type='mex_inference', pipeline_stage=2|3|4)

io.yaml 样例 (约定):
```yaml
models:
  - name: orders_risk_model
    version: 1
    input:
      fqn: gcs://company-model-input/orders/2026/
      format: parquet
    output:
      fqn: gcs://company-model-output/orders/2026/
      format: parquet
    owner: ml-team@example.com
    schedule: "0 2 * * *"
    description: 订单风控模型, 输出风险评分
```

Inputs:
    repo: str            # 例如 "company/mex-models"
    paths: list[str]     # 例如 ["orders/io.yaml", "orders/README.md"]
    ref: str = "HEAD"
    apply: bool = True   # 直接写 lineage vs ai_suggestion
    pipeline_stage: int = 3  # MEX 所在阶段 (固定 3)

Outputs (SkillOutput.items):
    [{model, input_fqn, output_fqn, stage, confidence, source, suggestion_id|lineage_id}, ...]
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

import yaml
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
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return owner.strip(), name.strip()
    return "", repo.strip()


def _parse_mex_io_yaml(content: str) -> list[dict[str, Any]]:
    """解析 MEX io.yaml, 返回 [{name, input_fqn, output_fqn, ...}, ...]."""
    if not content.strip():
        return []
    try:
        doc = yaml.safe_load(content)
    except yaml.YAMLError:
        return []
    if not isinstance(doc, dict):
        return []
    models = doc.get("models", [])
    if not isinstance(models, list):
        return []
    out: list[dict[str, Any]] = []
    for m in models:
        if not isinstance(m, dict):
            continue
        name = m.get("name")
        if not name:
            continue
        input_cfg = m.get("input") or {}
        output_cfg = m.get("output") or {}
        input_fqn = input_cfg.get("fqn") if isinstance(input_cfg, dict) else None
        output_fqn = output_cfg.get("fqn") if isinstance(output_cfg, dict) else None
        out.append(
            {
                "name": str(name),
                "version": m.get("version", 1),
                "input_fqn": str(input_fqn) if input_fqn else None,
                "output_fqn": str(output_fqn) if output_fqn else None,
                "input_format": input_cfg.get("format") if isinstance(input_cfg, dict) else None,
                "output_format": output_cfg.get("format") if isinstance(output_cfg, dict) else None,
                "owner": m.get("owner"),
                "schedule": m.get("schedule"),
                "description": m.get("description"),
            }
        )
    return out


async def _upsert_pipeline(
    db: AsyncSession, name: str, model_def: dict[str, Any], source_url: str
) -> UUID:
    """upsert pipelines (type='mex_model', stage=3)."""
    stmt = select(Pipeline).where(Pipeline.name == name, Pipeline.type == "mex_model")
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is not None:
        # 增量更新 description / config
        if model_def.get("description"):
            row.description = model_def["description"]
        if model_def:
            row.config = {**(row.config or {}), **model_def}
        return row.id
    row = Pipeline(
        id=uuid4(),
        name=name,
        type="mex_model",
        source_code_url=source_url,
        description=model_def.get("description") or f"MEX 模型: {name}",
        config={
            "version": model_def.get("version", 1),
            "input_fqn": model_def.get("input_fqn"),
            "output_fqn": model_def.get("output_fqn"),
            "input_format": model_def.get("input_format"),
            "output_format": model_def.get("output_format"),
            "owner": model_def.get("owner"),
            "schedule": model_def.get("schedule"),
        },
    )
    db.add(row)
    await db.flush()
    return row.id


async def _ensure_mex_namespace(db: AsyncSession) -> UUID:
    """为 MEX IO 端口准备 service -> database -> schema (idempotent).

    路径: service='mex' -> database='mex_models' -> schema='default'
    """
    stmt = select(Service).where(Service.name == "mex")
    svc = (await db.execute(stmt)).scalar_one_or_none()
    if svc is None:
        svc = Service(id=uuid4(), name="mex", type="mex",
                      description="MEX (Model Execution) 黑盒模型服务")
        db.add(svc)
        await db.flush()

    stmt = select(Database).where(Database.service_id == svc.id, Database.name == "mex_models")
    db_obj = (await db.execute(stmt)).scalar_one_or_none()
    if db_obj is None:
        db_obj = Database(id=uuid4(), service_id=svc.id, name="mex_models",
                          description="MEX 模型 IO 声明集合")
        db.add(db_obj)
        await db.flush()

    stmt = select(Schema).where(Schema.database_id == db_obj.id, Schema.name == "default")
    sch = (await db.execute(stmt)).scalar_one_or_none()
    if sch is None:
        sch = Schema(id=uuid4(), database_id=db_obj.id, name="default",
                     description="MEX IO 端口默认 schema")
        db.add(sch)
        await db.flush()
    return sch.id


async def _lookup_or_create_mex_io_asset(
    db: AsyncSession, fqn: str, name: str, kind: str
) -> UUID | None:
    """MEX 输入/输出端口在 KG 中以 table_asset (asset_subtype='mex_io') 表达.
    kind: 'in' | 'out'
    """
    stmt = select(TableAsset).where(TableAsset.fqn == fqn)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is not None:
        return row.id
    sch_id = await _ensure_mex_namespace(db)
    row = TableAsset(
        id=uuid4(),
        schema_id=sch_id,
        name=name,
        fqn=fqn,
        asset_type="table",
        asset_subtype="mex_io",
        external_ref=f"mex://{fqn}",
        tier="normal",
        status="active",
        description=f"MEX {kind} port: {fqn}",
        description_source="mex_io_parse",
        column_count=0,
        extra={"mex_io_kind": kind, "detected_from": "parse_mex_io"},
    )
    db.add(row)
    await db.flush()
    return row.id


@skill(name="parse_mex_io", version=1, agent="lineage")
async def parse_mex_io(ctx: SkillContext, **inputs: Any) -> SkillResult:
    """解析 MEX 黑盒声明 (io.yaml) → 写 pipeline + 血缘.

    适用 6 阶段管道阶段 3 (MEX), 输出在阶段 4, 输入来自阶段 2。
    """
    repo: str = inputs.get("repo") or ""
    paths: list[str] = inputs.get("paths") or []
    ref: str = inputs.get("ref") or "HEAD"
    apply: bool = bool(inputs.get("apply", True))
    pipeline_stage: int = int(inputs.get("pipeline_stage") or 3)

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
    use_local = gh._use_local  # M1.5 演示
    if not gh.has_token and not use_local:
        return SkillResult(
            ok=False,
            output=SkillOutput(),
            error="MCP_GITHUB_TOKEN not set and MOCK_GITHUB_ROOT is empty",
        )

    items: list[dict[str, Any]] = []
    skipped = 0
    for p in paths:
        if not p.lower().endswith((".yaml", ".yml")):
            continue
        try:
            if use_local:
                content = await gh.get_file_local(owner=owner, repo=repo_name, ref=ref, path=p)
            else:
                content = await gh.get_file(owner=owner, repo=repo_name, ref=ref, path=p)
        except Exception:  # noqa: BLE001
            skipped += 1
            continue

        models = _parse_mex_io_yaml(content)
        if not models:
            skipped += 1
            continue

        source_url = f"github://{owner}/{repo_name}/{p}"
        for m in models:
            name = m["name"]
            input_fqn = m.get("input_fqn")
            output_fqn = m.get("output_fqn")

            # 1) 写 pipeline 实体 (type=mex_model, stage=3)
            pipeline_id = await _upsert_pipeline(
                db=ctx.db, name=name, model_def=m, source_url=source_url
            )

            # 2) 写 IO 端口 asset
            in_asset_id = None
            out_asset_id = None
            mex_in_fqn = f"mex://{name}/in"
            mex_out_fqn = f"mex://{name}/out"
            in_asset_id = await _lookup_or_create_mex_io_asset(
                db=ctx.db, fqn=mex_in_fqn, name=f"{name}__in", kind="in"
            )
            out_asset_id = await _lookup_or_create_mex_io_asset(
                db=ctx.db, fqn=mex_out_fqn, name=f"{name}__out", kind="out"
            )

            # 3) 写血缘边
            edges_to_write = []
            if input_fqn and in_asset_id:
                # 阶段 2 -> 3: gcs(model-input) -> mex://name/in
                edges_to_write.append(
                    {
                        "upstream_fqn": input_fqn,
                        "downstream_fqn": mex_in_fqn,
                        "transform_type": "mex_inference",
                        "transform_subtype": "gcs_to_mex",
                        "pipeline_stage": pipeline_stage - 1,  # 阶段 2
                    }
                )
            if in_asset_id and out_asset_id:
                # 阶段 3 内部: mex://name/in -> mex://name/out
                edges_to_write.append(
                    {
                        "upstream_fqn": mex_in_fqn,
                        "downstream_fqn": mex_out_fqn,
                        "transform_type": "mex_inference",
                        "transform_subtype": "mex_internal",
                        "pipeline_stage": pipeline_stage,
                    }
                )
            if out_asset_id and output_fqn:
                # 阶段 3 -> 4: mex://name/out -> gcs(model-output)
                edges_to_write.append(
                    {
                        "upstream_fqn": mex_out_fqn,
                        "downstream_fqn": output_fqn,
                        "transform_type": "mex_inference",
                        "transform_subtype": "mex_to_gcs",
                        "pipeline_stage": pipeline_stage + 1,  # 阶段 4
                    }
                )

            for e in edges_to_write:
                if apply:
                    up_stmt = select(TableAsset).where(TableAsset.fqn == e["upstream_fqn"])
                    down_stmt = select(TableAsset).where(TableAsset.fqn == e["downstream_fqn"])
                    up_row = (await ctx.db.execute(up_stmt)).scalar_one_or_none()
                    down_row = (await ctx.db.execute(down_stmt)).scalar_one_or_none()
                    if up_row is not None and down_row is not None:
                        lineage = TableLineage(
                            id=uuid4(),
                            upstream_id=up_row.id,
                            downstream_id=down_row.id,
                            transform_type=e["transform_type"],
                            transform_subtype=e["transform_subtype"],
                            confidence=0.8,
                            source="github_mcp",
                            job_id=str(pipeline_id),
                            extra={
                                "mex_model": name,
                                "mex_yaml": p,
                                "ref": ref,
                            },
                        )
                        ctx.db.add(lineage)
                        items.append({**e, "mex_model": name, "lineage_id": str(lineage.id)})
                else:
                    sug = AISuggestion(
                        suggestion_type="lineage_inferred",
                        target_type="lineage",
                        target_id=uuid4(),
                        payload={**e, "mex_model": name, "mex_yaml": p, "ref": ref},
                        rationale=f"MEX io.yaml 推断: {e['upstream_fqn']} → {e['downstream_fqn']}",
                        confidence=0.75,
                        model="github_mcp",
                        skill="parse_mex_io",
                        use_case_id=ctx.use_case_id,
                        status="pending",
                    )
                    ctx.db.add(sug)
                    items.append({**e, "mex_model": name, "suggestion_id": str(sug.id)})

    if not items and skipped:
        return SkillResult(
            ok=True,
            output=SkillOutput(
                items=[],
                summary={"reason": "no io.yaml parsed", "skipped": skipped},
            ),
        )

    return SkillResult(
        ok=True,
        output=SkillOutput(
            items=items,
            summary={
                "mex_models": len({i["mex_model"] for i in items}),
                "edges": len(items),
                "skipped": skipped,
            },
        ),
    )
