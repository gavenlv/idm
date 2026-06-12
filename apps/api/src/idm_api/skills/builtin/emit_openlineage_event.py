"""emit_openlineage_event: 翻译 IDM 血缘为 OpenLineage 兼容事件 (M2.5 新增).

设计: docs/design/openlineage-alignment.md

参考业界标准: https://openlineage.io/

策略:
1. 输入: pipeline_run_id (单 run) 或 (job_namespace + job_name + run_id) (单 job)
2. 读 IDM 内部表 (table_lineage + column_lineage + table_asset)
3. 翻译为 OpenLineage RunEvent JSON (含 ColumnLineageDatasetFacet)
4. 写 lineage_event 表 (审计 + OL export)

输入参数:
  - pipeline_run_id: 某次 pipeline run (优先)
  - job_namespace: e.g. "airflow-prod"
  - job_name: e.g. "etl_orders_daily"
  - run_id: OpenLineage Run.runId
  - event_type: START | RUNNING | COMPLETE | FAIL | ABORT (默认 COMPLETE)
  - dry_run: bool (默认 False)

输出: SkillOutput(summary={
  "events_emitted": int,
  "inputs_count": int,
  "outputs_count": int,
  "column_lineage_facets": int,
})
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill
from idm_kg.models.column_asset import ColumnAsset
from idm_kg.models.column_lineage import ColumnLineage
from idm_kg.models.lineage_event import LineageEvent
from idm_kg.models.pipeline import PipelineRun
from idm_kg.models.table_asset import TableAsset
from idm_kg.models.table_lineage import TableLineage

logger = logging.getLogger(__name__)


# === IDM transform_type → OpenLineage transformation type ===
# OpenLineage spec: https://openlineage.io/spec/facets/DatasetFacets/column_lineage_facet/
_TRANSFORM_TYPE_MAP: dict[str, tuple[str, str]] = {
    # (idm_type) -> (ol_type, ol_subtype)
    "direct": ("DIRECT", None),
    "passthrough": ("DIRECT", "IDENTITY"),
    "rename": ("DIRECT", "RENAME"),
    "cast": ("TRANSFORMATION", "CAST"),
    "aggregation": ("TRANSFORMATION", "AGGREGATION"),
    "expression": ("TRANSFORMATION", "EXPRESSION"),
    "derivation": ("TRANSFORMATION", "DERIVATION"),
}


def _default_ol_namespace(table: TableAsset) -> str:
    """从 table_asset 派生 OpenLineage namespace (fallback)."""
    if table.ol_namespace:
        return table.ol_namespace
    # fallback: <service>://<database>
    return f"{table.service}://{table.database}"


def _build_dataset(table: TableAsset) -> dict[str, Any]:
    """构建 OpenLineage Dataset 对象 (含 columnLineage facet)."""
    return {
        "namespace": _default_ol_namespace(table),
        "name": table.name,
        "facets": {
            "documentation": {
                "_producer": "idm/0.4.0",
                "description": table.description or "",
            },
        },
    }


def _build_column_lineage_facet_for_input(
    table: TableAsset,
    col_edges: list[ColumnLineage],
    up_cols: dict[uuid.UUID, ColumnAsset],
    down_cols: dict[uuid.UUID, ColumnAsset],
) -> dict[str, Any]:
    """构建 OpenLineage ColumnLineageDatasetFacet.fields (从下游列反查所有上游)."""
    fields: dict[str, Any] = {}

    for edge in col_edges:
        up_col = up_cols.get(edge.upstream_column_id)
        down_col = down_cols.get(edge.downstream_column_id)
        if not up_col or not down_col:
            continue
        up_table = (
            TableAsset(service="?", database="?", schema="default", name="?")
            if edge.upstream_table_id != table.id
            else table
        )
        # 用 inputField 表示
        up_table_obj = _build_dataset(up_table)
        input_field = {
            "namespace": up_table_obj["namespace"],
            "name": up_table_obj["name"],
            "field": up_col.name,
        }

        # 转换类型映射
        ol_type, ol_subtype = _TRANSFORM_TYPE_MAP.get(
            edge.transform_type, ("TRANSFORMATION", edge.transform_type.upper())
        )
        transformation: dict[str, Any] = {
            "type": ol_type,
            "subtype": ol_subtype,
            "description": edge.description or "",
            "expression": edge.transform_expression or "",
            "masking": False,
        }

        # 累加到下游列
        if down_col.name not in fields:
            fields[down_col.name] = {
                "inputFields": [input_field],
                "transformations": [transformation],
            }
        else:
            # 已有, 追加 inputField
            if input_field not in fields[down_col.name]["inputFields"]:
                fields[down_col.name]["inputFields"].append(input_field)
            # transformation 也追加 (如果不同)
            if transformation not in fields[down_col.name]["transformations"]:
                fields[down_col.name]["transformations"].append(transformation)

    return fields


def _build_ol_event(
    *,
    event_type: str,
    event_time: datetime,
    job_namespace: str,
    job_name: str,
    run_id: str,
    inputs: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    facets: dict[str, Any],
    producer: str,
) -> dict[str, Any]:
    """构造 OpenLineage 1.0+ RunEvent JSON (符合 https://openlineage.io/spec/)."""
    return {
        "eventType": event_type,
        "eventTime": event_time.isoformat(),
        "producer": producer,
        "schemaURL": "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent",
        "job": {
            "namespace": job_namespace,
            "name": job_name,
        },
        "run": {
            "runId": run_id,
        },
        "inputs": inputs,
        "outputs": outputs,
        "jobFacets": {},
        "runFacets": facets,
    }


@skill(name="emit_openlineage_event", version=1, agent="lineage")
async def emit_openlineage_event(ctx: SkillContext, **inputs: Any) -> SkillResult:
    """翻译 IDM 血缘为 OpenLineage 兼容事件, 写 lineage_event 表.

    输入 (二选一):
      A) pipeline_run_id: str (UUID)  — 翻译该 run 涉及的所有 table_lineage
      B) job_namespace + job_name + run_id: 自定义

    可选:
      - event_type: START | RUNNING | COMPLETE | FAIL | ABORT (默认 COMPLETE)
      - dry_run: bool (默认 False)
    """
    db: AsyncSession = ctx.db

    # === 1. 解析参数 ===
    pipeline_run_id_str = inputs.get("pipeline_run_id")
    job_namespace = inputs.get("job_namespace")
    job_name = inputs.get("job_name")
    run_id = inputs.get("run_id")
    event_type = (inputs.get("event_type") or "COMPLETE").upper()
    dry_run = bool(inputs.get("dry_run", False))

    pipeline_run_obj: PipelineRun | None = None
    if pipeline_run_id_str:
        try:
            pipeline_run_obj = await db.get(PipelineRun, uuid.UUID(str(pipeline_run_id_str)))
        except (ValueError, TypeError):
            pass
        if pipeline_run_obj is None:
            return SkillResult(
                ok=False,
                output=SkillOutput(summary={"error": f"pipeline_run {pipeline_run_id_str} not found"}),
                error="pipeline_run_not_found",
            )
        # 从 PipelineRun 派生 job namespace/name
        pipeline = pipeline_run_obj.pipeline
        job_namespace = job_namespace or (f"idm://{pipeline.name}" if pipeline else "idm://unknown")
        job_name = job_name or (pipeline.name if pipeline else "unknown")
        run_id = run_id or pipeline_run_obj.external_id or str(pipeline_run_obj.id)
    else:
        if not (job_namespace and job_name and run_id):
            return SkillResult(
                ok=False,
                output=SkillOutput(
                    summary={"error": "missing required: job_namespace + job_name + run_id"}
                ),
                error="missing_inputs",
            )

    # 验证 event_type
    if event_type not in {"START", "RUNNING", "COMPLETE", "FAIL", "ABORT"}:
        return SkillResult(
            ok=False,
            output=SkillOutput(summary={"error": f"invalid event_type: {event_type}"}),
            error="invalid_event_type",
        )

    # === 2. 读 table_lineage (找所有涉及的表) ===
    # 简化: 取最近 1 小时内 active 的所有 table_lineage (实际应按 pipeline_run 关联)
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    edges_stmt = select(TableLineage).where(TableLineage.created_at >= cutoff)
    edges = list((await db.execute(edges_stmt)).scalars())
    if not edges:
        return SkillResult(
            ok=True,
            output=SkillOutput(
                summary={"events_emitted": 0, "inputs_count": 0, "outputs_count": 0}
            ),
        )

    # 收集所有涉及到的 table_id
    table_ids: set[uuid.UUID] = set()
    for e in edges:
        table_ids.add(e.upstream_id)
        table_ids.add(e.downstream_id)
    tables = list(
        (await db.execute(select(TableAsset).where(TableAsset.id.in_(table_ids)))).scalars()
    )
    table_by_id: dict[uuid.UUID, TableAsset] = {t.id: t for t in tables}

    # === 3. 读 column_lineage (与 table_lineage 配对) ===
    col_edges = list(
        (
            await db.execute(
                select(ColumnLineage).where(
                    ColumnLineage.upstream_table_id.in_(table_ids),
                    ColumnLineage.downstream_table_id.in_(table_ids),
                )
            )
        ).scalars()
    )

    col_ids: set[uuid.UUID] = set()
    for e in col_edges:
        col_ids.add(e.upstream_column_id)
        col_ids.add(e.downstream_column_id)
    cols = list(
        (await db.execute(select(ColumnAsset).where(ColumnAsset.id.in_(col_ids)))).scalars()
    )
    col_by_id: dict[uuid.UUID, ColumnAsset] = {c.id: c for c in cols}

    # === 4. 按 (upstream, downstream) 配对 edges + 收集 dataset ===
    # 用一个 dict: downstream_table_id -> list[ColumnLineage]
    col_edges_by_down_table: dict[uuid.UUID, list[ColumnLineage]] = {}
    for e in col_edges:
        col_edges_by_down_table.setdefault(e.downstream_table_id, []).append(e)

    inputs_ol: list[dict[str, Any]] = []
    outputs_ol: list[dict[str, Any]] = []
    upstream_ids: set[uuid.UUID] = set()
    downstream_ids: set[uuid.UUID] = set()

    for edge in edges:
        upstream_ids.add(edge.upstream_id)
        downstream_ids.add(edge.downstream_id)

    # 构建 input datasets (含 columnLineage facet)
    for tid in upstream_ids:
        t = table_by_id.get(tid)
        if not t:
            continue
        ds = _build_dataset(t)
        ds["facets"]["columnLineage"] = {
            "_producer": "idm/0.4.0",
            "fields": _build_column_lineage_facet_for_input(
                t, col_edges_by_down_table.get(tid, []), col_by_id, col_by_id
            ),
        }
        inputs_ol.append(ds)

    # 构建 output datasets
    for tid in downstream_ids:
        t = table_by_id.get(tid)
        if not t:
            continue
        ds = _build_dataset(t)
        outputs_ol.append(ds)

    # === 5. 构造 OL 事件 ===
    event_time = datetime.now(timezone.utc)
    producer = "idm/0.4.0 (idm-skill/emit_openlineage_event)"
    facets = {
        "parent": {},
        "processing_engine": {"name": "idm", "version": "0.4.0"},
    }
    ol_event = _build_ol_event(
        event_type=event_type,
        event_time=event_time,
        job_namespace=job_namespace,
        job_name=job_name,
        run_id=run_id,
        inputs=inputs_ol,
        outputs=outputs_ol,
        facets=facets,
        producer=producer,
    )

    # === 6. 写 lineage_event (除非 dry_run) ===
    n_emitted = 0
    if not dry_run:
        event_row = LineageEvent(
            event_type=event_type,
            event_time=event_time,
            job_namespace=job_namespace,
            job_name=job_name,
            run_id=run_id,
            inputs=inputs_ol,
            outputs=outputs_ol,
            facets=facets,
            producer=producer,
            source_skill="emit_openlineage_event",
            pipeline_run_id=pipeline_run_obj.id if pipeline_run_obj else None,
            extra={"ol_schema": ol_event["schemaURL"]},
        )
        db.add(event_row)
        await db.commit()
        await db.refresh(event_row)
        n_emitted = 1

    n_col_facets = sum(
        1 for ds in inputs_ol if ds.get("facets", {}).get("columnLineage", {}).get("fields")
    )

    return SkillResult(
        ok=True,
        output=SkillOutput(
            items=[ol_event],
            summary={
                "events_emitted": n_emitted,
                "inputs_count": len(inputs_ol),
                "outputs_count": len(outputs_ol),
                "column_lineage_facets": n_col_facets,
                "event_type": event_type,
                "job_namespace": job_namespace,
                "job_name": job_name,
                "run_id": run_id,
                "ol_schema": ol_event["schemaURL"],
            },
            artifacts=[str(event_row.id)] if not dry_run and n_emitted else [],
        ),
    )
