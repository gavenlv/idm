"""discover_gcs_assets: 从 GCS 扫 parquet/csv/json 文件 → 推断 schema → 写 gcs_object + table_asset (asset_subtype=gcs_object).

适用 6 阶段真实管道阶段 1 (上游) / 2 (model-input) / 4 (model-output).

Inputs:
    bucket: str
    prefix: str = ""             # GCS path prefix (例如 orders/2026/)
    format_filter: list[str] = ["parquet", "csv", "json"]
    max_objects: int = 200
    infer_schema: bool = True
    apply: bool = True           # True=写 KG; False=写 ai_suggestion
    stage: int = None            # 6 阶段管道标号 (1|2|4), 写到 gcs_objects.pipeline_stage + table_assets.pipeline_stage
    source_role: str = None      # 'raw' | 'model_input' | 'model_output' (可选, 用于更明确的语义)

Outputs (SkillOutput.items):
    [{fqn, bucket, key, format, size_bytes, row_count_estimate, schema_columns, table_id, stage, source_role}, ...]

写入:
    gcs_objects 表 (upsert, pipeline_stage=stage)
    table_assets (asset_subtype='gcs_object', pipeline_stage=stage)  — 同样 fqn 重复用
    第一次写: 创建 fake schema (放在 service='gcs', database=bucket, schema='default')
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.skills.mcp import get_gcs_mcp
from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill
from idm_kg.models.ai_suggestion import AISuggestion
from idm_kg.models.database import Database
from idm_kg.models.pipeline import GcsObject
from idm_kg.models.schema import Schema
from idm_kg.models.service import Service
from idm_kg.models.table_asset import TableAsset

logger = logging.getLogger(__name__)


async def _ensure_service_db_schema(
    db: AsyncSession, service_name: str, db_name: str
) -> tuple[UUID, UUID, UUID]:
    """为 GCS 资产准备 service -> database -> schema 三层 (idempotent)."""
    # 1) Service
    stmt = select(Service).where(Service.name == service_name)
    svc = (await db.execute(stmt)).scalar_one_or_none()
    if svc is None:
        svc = Service(id=uuid4(), name=service_name, type="gcs", description=f"GCS bucket {service_name}")
        db.add(svc)
        await db.flush()

    # 2) Database (= bucket)
    stmt = select(Database).where(Database.service_id == svc.id, Database.name == db_name)
    db_obj = (await db.execute(stmt)).scalar_one_or_none()
    if db_obj is None:
        db_obj = Database(id=uuid4(), service_id=svc.id, name=db_name, description=f"GCS bucket {db_name}")
        db.add(db_obj)
        await db.flush()

    # 3) Schema (固定 'default')
    stmt = select(Schema).where(Schema.database_id == db_obj.id, Schema.name == "default")
    sch = (await db.execute(stmt)).scalar_one_or_none()
    if sch is None:
        sch = Schema(id=uuid4(), database_id=db_obj.id, name="default")
        db.add(sch)
        await db.flush()

    return svc.id, db_obj.id, sch.id


def _infer_format(key: str) -> str | None:
    key = key.lower()
    for fmt in ("parquet", "csv", "json", "orc", "avro"):
        if key.endswith(f".{fmt}"):
            return fmt
    return None


def _validate_stage(stage: int | None) -> int | None:
    """6 阶段管道标号 1|2|3|4|5|6; GCS 资产只可能在 1, 2, 4 阶段."""
    if stage is None:
        return None
    stage = int(stage)
    if stage not in (1, 2, 4):
        raise ValueError(f"stage must be 1|2|4 for GCS asset, got {stage}")
    return stage


@skill(name="discover_gcs_assets", version=2, agent="schema")
async def discover_gcs_assets(ctx: SkillContext, **inputs: Any) -> SkillResult:
    bucket: str = inputs.get("bucket") or ""
    prefix: str = inputs.get("prefix") or ""
    format_filter: list[str] = inputs.get("format_filter") or ["parquet", "csv", "json"]
    max_objects: int = int(inputs.get("max_objects") or 200)
    infer_schema: bool = bool(inputs.get("infer_schema", True))
    apply: bool = bool(inputs.get("apply", True))
    # === 6 阶段管道 (2026-06-08 M1.5 强化) ===
    stage: int | None = None
    if inputs.get("stage") is not None:
        try:
            stage = _validate_stage(inputs["stage"])
        except ValueError as e:
            return SkillResult(ok=False, output=SkillOutput(), error=str(e))
    source_role: str | None = inputs.get("source_role")  # raw / model_input / model_output

    if not bucket:
        return SkillResult(ok=False, output=SkillOutput(), error="bucket is required")
    if ctx.db is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.db is None")

    gcs = get_gcs_mcp()
    health = gcs.health()
    if health.get("status") not in ("ok", "mock"):
        return SkillResult(ok=False, output=SkillOutput(), error=f"GCS not ready: {health}")

    objects = gcs.list_objects(bucket=bucket, prefix=prefix, max_results=max_objects)
    if not objects:
        return SkillResult(
            ok=True,
            output=SkillOutput(items=[], summary={"reason": "no objects in bucket/prefix", "stage": stage}),
        )

    # 过滤格式
    objects = [o for o in objects if o["key"].split(".")[-1].lower() in format_filter]
    if not objects:
        return SkillResult(
            ok=True,
            output=SkillOutput(items=[], summary={"reason": f"no objects matched format {format_filter}", "stage": stage}),
        )

    # 准备 service/database/schema
    _, _, sch_id = await _ensure_service_db_schema(ctx.db, service_name="gcs", db_name=bucket)

    items: list[dict[str, Any]] = []
    skipped = 0
    llm_calls = 0

    for o in objects:
        fqn = o["fqn"]  # gcs://bucket/key
        fmt = _infer_format(o["key"])
        if not fmt:
            skipped += 1
            continue

        # 1) 推断 schema (轻量: 仅前 1MB)
        schema_columns: list[dict[str, Any]] = []
        if infer_schema:
            try:
                schema_columns = gcs.infer_schema(bucket=bucket, key=o["key"], sample_rows=1000)
            except Exception:  # noqa: BLE001
                schema_columns = []

        # 2) upsert gcs_object
        gcs_row = (
            await ctx.db.execute(select(GcsObject).where(GcsObject.fqn == fqn))
        ).scalar_one_or_none()
        if gcs_row is None:
            gcs_row = GcsObject(
                id=uuid4(),
                bucket=bucket,
                key=o["key"],
                fqn=fqn,
                format=fmt,
                size_bytes=o.get("size"),
                row_count_estimate=None,
                schema_json=schema_columns,
                pipeline_stage=stage,
                first_seen=datetime.utcnow(),
                last_modified=datetime.fromisoformat(o["updated"]) if o.get("updated") else None,
                profiled_at=datetime.utcnow() if schema_columns else None,
            )
            ctx.db.add(gcs_row)
        else:
            gcs_row.size_bytes = o.get("size") or gcs_row.size_bytes
            gcs_row.last_modified = datetime.fromisoformat(o["updated"]) if o.get("updated") else gcs_row.last_modified
            if schema_columns:
                gcs_row.schema_json = schema_columns
                gcs_row.profiled_at = datetime.utcnow()
            if stage is not None:
                gcs_row.pipeline_stage = stage

        # 3) upsert table_asset (asset_subtype='gcs_object')
        try:
            extra_payload: dict[str, Any] = {"format": fmt, "bucket": bucket, "key": o["key"]}
            if source_role:
                extra_payload["source_role"] = source_role
            stmt_ins = (
                pg_insert(TableAsset)
                .values(
                    id=uuid4(),
                    schema_id=sch_id,
                    name=o["key"].split("/")[-1],
                    fqn=fqn,
                    asset_type="table",
                    asset_subtype="gcs_object",
                    external_ref=fqn,
                    tier="normal",
                    status="active",
                    description=f"GCS object {fqn} (format={fmt}, size={o.get('size')}, stage={stage})",
                    description_source="gcs_mcp",
                    column_count=len(schema_columns),
                    row_count=None,
                    size_bytes=o.get("size"),
                    last_profiled_at=datetime.utcnow() if schema_columns else None,
                    pipeline_stage=stage,
                    extra=extra_payload,
                )
                .on_conflict_do_nothing(index_elements=["fqn"])
            )
            await ctx.db.execute(stmt_ins)
        except IntegrityError:
            await ctx.db.rollback()
            skipped += 1
            continue

        # 拿回 table_asset 的 id
        table_row = (
            await ctx.db.execute(select(TableAsset).where(TableAsset.fqn == fqn))
        ).scalar_one_or_none()
        table_id = str(table_row.id) if table_row else None

        # 4) (可选) ai_suggestion 写 schema 推断建议
        if not apply and ctx.llm:
            llm_calls += 1
            # 简化: 不调 LLM, 直接 pending
            sug = AISuggestion(
                suggestion_type="schema_inferred",
                target_type="table",
                target_id=UUID(table_id) if table_id else uuid4(),
                payload={"fqn": fqn, "format": fmt, "schema": schema_columns, "stage": stage},
                rationale=f"GCS 推断: {fmt} 格式, {len(schema_columns)} 列, stage={stage}",
                confidence=0.7,
                model="gcs_mcp",
                skill="discover_gcs_assets",
                use_case_id=ctx.use_case_id,
                status="pending",
            )
            ctx.db.add(sug)

        items.append(
            {
                "fqn": fqn,
                "bucket": bucket,
                "key": o["key"],
                "format": fmt,
                "size_bytes": o.get("size"),
                "schema_columns": len(schema_columns),
                "table_id": table_id,
                "gcs_object_id": str(gcs_row.id),
                "stage": stage,
                "source_role": source_role,
            }
        )

    await ctx.db.commit()

    return SkillResult(
        ok=True,
        output=SkillOutput(
            items=items,
            summary={
                "bucket": bucket,
                "prefix": prefix,
                "objects_scanned": len(objects),
                "assets_created": len(items),
                "skipped": skipped,
                "infer_schema": infer_schema,
                "llm_calls": llm_calls,
                "gcs_mode": health.get("mode"),
                "stage": stage,
                "source_role": source_role,
            },
        ),
    )
