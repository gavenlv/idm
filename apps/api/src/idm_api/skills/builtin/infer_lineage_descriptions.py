"""infer_lineage_descriptions: 推断血缘边的组件级描述 (M2.x 新增).

策略: 80% 组件模板 (component + transform_type + transform_subtype) + 20% LLM 兜底
输入: table_lineage edges (可限定 table_id)
输出: ai_suggestion (suggestion_type=description, target_type=lineage)
      写入 table_lineage.description + column_lineage.description
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill
from idm_kg.models.ai_suggestion import AISuggestion
from idm_kg.models.column_lineage import ColumnLineage
from idm_kg.models.table_asset import TableAsset
from idm_kg.models.table_lineage import TableLineage

logger = logging.getLogger(__name__)


# === 组件描述模板 ===
COMPONENT_TEMPLATES: dict[tuple[str, str], str] = {
    # (component, transform_type) -> template
    ("airflow_task", "copy"):        "由 Airflow DAG `{dag_id}` 的 task `{task_id}` 复制到下游",
    ("airflow_task", "filter"):      "由 Airflow `{dag_id}.{task_id}` 按条件 `{filter_expr}` 过滤",
    ("airflow_task", "cast"):        "由 Airflow `{dag_id}.{task_id}` 类型转换 (`{transform_expr}`)",
    ("airflow_task", "expression"):  "由 Airflow `{dag_id}.{task_id}` 派生 (`{transform_expr}`)",
    ("airflow_task", "aggregation"): "由 Airflow `{dag_id}.{task_id}` 聚合 (`{transform_expr}`)",
    ("dbt_model", "ref"):            "dbt model `{model_name}` 引用 `{upstream_fqn}` 构建",
    ("dbt_model", "expression"):     "dbt model `{model_name}` 派生 (`{transform_expr}`)",
    ("mex_model", "io"):             "MEX 黑盒模型 `{model_name}` 读 `{upstream_fqn}`, 写 `{downstream_fqn}`",
    ("mex_model", "expression"):     "MEX 模型 `{model_name}` 派生表达式 `{transform_expr}`",
    ("mex_model", "inference"):      "MEX 模型 `{model_name}` 推理: `{transform_expr}`",
    ("flink_job", "sql"):            "由 Flink Job `{job_id}` SQL `{sql_glimpse}` 转换生成",
    ("flink_job", "aggregation"):    "Flink Job `{job_id}` 聚合 (`{transform_expr}`)",
    ("superset_chart", "query"):     "Superset chart `{chart_id}` 查表 `{upstream_fqn}`, 字段: `{columns}`",
    ("superset_chart", "derivation"): "由 Superset chart `{chart_id}` 虚拟列派生 (`{transform_expr}`)",
    ("sql", "cte"):                  "由 SQL `{sql_glimpse}` 转换生成",
    ("sql", "expression"):           "SQL 派生表达式 `{transform_expr}`",
    ("gcs_copy", "copy"):            "GCS 复制 `{upstream_fqn}` → `{downstream_fqn}`",
    ("clickhouse_table", "view"):    "ClickHouse view `{downstream_fqn}` 引用 `{upstream_fqn}`",
}


def _truncate(s: str | None, n: int = 80) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[: n - 3] + "..."


def _gen_table_lineage_desc(edge: TableLineage, upstream: TableAsset, downstream: TableAsset) -> tuple[str, float, str]:
    """生成表级血缘边的组件级描述."""
    component = edge.component or "sql"
    transform_type = edge.transform_type
    transform_subtype = edge.transform_subtype
    transform_expr = _truncate(edge.transform_expression, 100)
    sql_glimpse = _truncate((edge.sql or "").replace("\n", " "), 80)
    job_id = edge.job_id or ""

    # 1) 模板匹配
    key = (component, transform_type)
    if key in COMPONENT_TEMPLATES:
        template = COMPONENT_TEMPLATES[key]
        try:
            desc = template.format(
                dag_id=job_id.split(".")[0] if "." in job_id else job_id,
                task_id=job_id.split(".")[-1] if "." in job_id else job_id,
                model_name=job_id,
                job_id=job_id,
                chart_id=job_id,
                upstream_fqn=upstream.fqn,
                downstream_fqn=downstream.fqn,
                transform_expr=transform_expr,
                sql_glimpse=sql_glimpse,
                filter_expr=transform_expr,
                columns=transform_expr or "(all)",
            )
            rationale = f"template_match:{component}/{transform_type}"
            return desc, 0.9, rationale  # 模板匹配高置信
        except KeyError:
            pass

    # 2) transform_subtype 模板 (e.g. flink_sql / airflow_task / mex_inference)
    if transform_subtype:
        key2 = (component, transform_subtype)
        if key2 in COMPONENT_TEMPLATES:
            template = COMPONENT_TEMPLATES[key2]
            try:
                desc = template.format(
                    dag_id=job_id,
                    task_id=job_id,
                    model_name=job_id,
                    job_id=job_id,
                    chart_id=job_id,
                    upstream_fqn=upstream.fqn,
                    downstream_fqn=downstream.fqn,
                    transform_expr=transform_expr or transform_subtype,
                    sql_glimpse=sql_glimpse,
                    filter_expr=transform_expr,
                    columns=transform_expr or "(all)",
                )
                rationale = f"template_match:{component}/{transform_subtype}"
                return desc, 0.8, rationale
            except KeyError:
                pass

    # 3) 通用兜底模板
    if transform_type == "copy":
        desc = f"由 {upstream.fqn} 复制到 {downstream.fqn}"
    elif transform_type == "aggregation":
        desc = f"{upstream.fqn} 聚合到 {downstream.fqn} ({transform_expr or 'aggregation'})"
    elif transform_type == "expression":
        desc = f"{upstream.fqn} 派生 {downstream.fqn} ({transform_expr or 'expression'})"
    elif transform_type == "derivation":
        desc = f"由 {upstream.fqn} 派生 {downstream.fqn}"
    else:
        desc = f"{upstream.fqn} → {downstream.fqn} ({component}/{transform_type})"
    return desc, 0.5, f"generic_template:{component}/{transform_type}"


@skill(name="infer_lineage_descriptions", version=1, agent="doc")
async def infer_lineage_descriptions(ctx: SkillContext, **inputs: Any) -> SkillResult:
    apply: bool = bool(inputs.get("apply", False))
    table_id: str | None = inputs.get("table_id")
    only_missing: bool = bool(inputs.get("only_missing", True))
    min_confidence: float = float(inputs.get("min_confidence") or 0.5)

    if ctx.db is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.db is None")

    # 1) 选边
    stmt = select(TableLineage)
    if table_id:
        stmt = stmt.where(
            (TableLineage.upstream_id == table_id) | (TableLineage.downstream_id == table_id)
        )
    if only_missing:
        stmt = stmt.where(TableLineage.description.is_(None))
    edges = list((await ctx.db.execute(stmt)).scalars())
    ctx.log("edges_selected", count=len(edges))

    if not edges:
        return SkillResult(
            ok=True,
            output=SkillOutput(items=[], summary={"reason": "no edges to describe"}),
        )

    items: list[dict[str, Any]] = []
    n_template = 0
    n_applied = 0
    n_skipped = 0
    n_col_derived = 0

    for edge in edges:
        up = await ctx.db.get(TableAsset, edge.upstream_id)
        down = await ctx.db.get(TableAsset, edge.downstream_id)
        if up is None or down is None:
            n_skipped += 1
            continue

        # 生成表级血缘描述
        desc, conf, rationale = _gen_table_lineage_desc(edge, up, down)
        if conf < min_confidence or not desc:
            n_skipped += 1
            continue
        n_template += 1

        # 写 ai_suggestion
        sug = AISuggestion(
            suggestion_type="description",
            target_type="lineage",
            target_id=edge.id,
            payload={"description": desc},
            rationale=rationale,
            confidence=conf,
            model="template",
            skill="infer_lineage_descriptions",
            use_case_id=ctx.use_case_id,
            status="pending",
        )
        ctx.db.add(sug)
        await ctx.db.flush()
        items.append(
            {
                "edge_id": str(edge.id),
                "upstream_fqn": up.fqn,
                "downstream_fqn": down.fqn,
                "description": desc,
                "confidence": conf,
                "rationale": rationale,
                "suggestion_id": str(sug.id),
            }
        )

        # apply=true 时直接写 (apply 模式下, 只要 min_confidence 通过就写)
        if apply and conf >= min_confidence:
            edge.description = desc
            edge.description_source = "ai_inferred"
            edge.description_rationale = rationale
            sug.status = "auto_applied"
            n_applied += 1

        # 同步给 column_lineage 推一份"上游 → 下游"汇总描述
        col_stmt = select(ColumnLineage).where(
            (ColumnLineage.upstream_table_id == up.id) & (ColumnLineage.downstream_table_id == down.id)
        ).where(ColumnLineage.job_id == edge.job_id)
        col_edges = list((await ctx.db.execute(col_stmt)).scalars())
        for ce in col_edges:
            if not ce.description and desc:
                # 简化: 用表级 description + transform_type
                ce.description = f"[{ce.transform_type}] {desc} (列 {ce.transform_expression[:60] if ce.transform_expression else ''})"
                ce.description_source = "ai_inferred"
                n_col_derived += 1

    await ctx.db.commit()

    summary = {
        "edges_processed": len(edges),
        "descriptions_generated": len(items),
        "template_matched": n_template,
        "auto_applied": n_applied,
        "skipped_low_confidence": n_skipped,
        "column_lineage_descriptions_derived": n_col_derived,
    }
    return SkillResult(
        ok=True,
        output=SkillOutput(items=items, summary=summary),
    )
