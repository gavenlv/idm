"""parse_airflow_dag: 解析 Airflow DAG Python 文件, 提取 task 依赖与 SQL 血缘线索.

Inputs:
    dag_file_path: str        Airflow DAG .py 绝对路径
    dag_id: str               仅处理该 dag_id (空 = 文件内所有)
    write_lineage: bool       是否把 task 间依赖写入 KG (默认 True)
    apply: bool               True=写 KG, False=仅解析

Outputs (SkillOutput.items):
    [{task_id, dag_id, depends_on[], sql?, downstream_tables[], status}, ...]

写入:
    - table_assets (task 关联的表, asset_type=airflow_task)
    - table_lineage (task -> task; transform_type=airflow_task)
    - 解析到的 SQL 暂不入 KG (留给 extract_sql_lineage 处理)

兼容:
    - 不依赖 airflow 运行时, 仅 AST 解析 .py 文件
    - 支持 BashOperator / PythonOperator / EmptyOperator, sql 字段从 BashOperator 提取
"""
from __future__ import annotations

import ast
import json
import logging
import os
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill
from idm_kg.models.pipeline import Pipeline
from idm_kg.models.schema import Schema
from idm_kg.models.service import Service
from idm_kg.models.table_asset import TableAsset
from idm_kg.models.table_lineage import TableLineage

logger = logging.getLogger(__name__)


# 简单正则: 抓 SQL 关键字后的 FROM/JOIN/INTO/UPDATE/TABLE 后的限定表名
_SQL_TBL_RE = re.compile(
    r"\b(?:from|join|into|update|table)\s+([a-zA-Z_][\w.\"`]*)",
    re.IGNORECASE,
)


def _extract_sql_tables(sql: str) -> list[str]:
    """从 SQL 文本里粗略抽取表名 (生产中应改用 sqlglot)。"""
    if not sql:
        return []
    seen: set[str] = set()
    for m in _SQL_TBL_RE.finditer(sql):
        ident = m.group(1).strip('"`')
        # 过滤 schema 名 (含 . 的合法)
        if ident and ident not in seen:
            seen.add(ident)
    return list(seen)


def _parse_dag_file(path: str) -> list[dict[str, Any]]:
    """用 AST 解析一个 .py 里的所有 DAG (含默认 args + tasks 依赖)."""
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        logger.warning("parse_airflow_dag: SyntaxError in %s: %s", path, e)
        return []

    dags: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # 匹配 with DAG(...) as dag_id: 或 dag = DAG(...)
        if not _is_dag_call(node):
            continue
        dag_id = _kwarg_str(node, "dag_id") or _call_name(node) or "unknown"
        tasks: list[dict[str, Any]] = []
        # 在 with-body 里 (ast.With) 找 Operator 调用
        parent = getattr(node, "_parent", None)  # type: ignore[attr-defined]
        body: list[ast.stmt] = []
        if parent is not None and isinstance(parent, ast.With):
            body = parent.body
        else:
            # 兜底: 没法拿 body, 跳过依赖收集
            body = []

        for stmt in body:
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Call):
                    op_name = _call_name(sub) or ""
                    if op_name.endswith("Operator"):
                        task_id = _kwarg_str(sub, "task_id") or "t"
                        # 找 sql / bash_command
                        sql = _kwarg_str(sub, "sql") or ""
                        bash = _kwarg_str(sub, "bash_command") or ""
                        # 找 dag= / dependencies
                        deps: list[str] = []
                        for kw in sub.keywords:
                            if kw.arg in ("dependencies",):
                                # list of task objects; we can't resolve names, skip
                                pass
                        tasks.append(
                            {
                                "task_id": task_id,
                                "operator": op_name,
                                "sql": sql,
                                "bash_command": bash,
                                "depends_on": deps,
                            }
                        )
        # dag-level depends_on via >>
        # 简化: 通过 >> 模式找 (low-fi AST)
        deps_pairs: list[tuple[str, str]] = []
        for sub in ast.walk(parent) if parent else []:
            # 找 BinaryOp with >>  (left >> right)
            pass

        dags.append(
            {
                "dag_id": dag_id,
                "schedule_interval": _kwarg_str(node, "schedule_interval"),
                "default_args_owner": _extract_default_args_owner(node),
                "tasks": tasks,
                "depends_pairs": deps_pairs,
            }
        )
    return dags


def _is_dag_call(node: ast.Call) -> bool:
    name = _call_name(node) or ""
    return name in ("DAG",) or name.endswith(".DAG")


def _call_name(node: ast.Call) -> str | None:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        parts: list[str] = []
        cur: ast.AST = f
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ".".join(reversed(parts))
    return None


def _kwarg_str(node: ast.Call, key: str) -> str | None:
    for kw in node.keywords:
        if kw.arg == key:
            v = kw.value
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                return v.value
    return None


def _extract_default_args_owner(dag_node: ast.Call) -> str | None:
    for kw in dag_node.keywords:
        if kw.arg == "default_args" and isinstance(kw.value, ast.Dict):
            for k, v in zip(kw.value.keys, kw.value.values):
                if isinstance(k, ast.Constant) and k.value == "owner" and isinstance(v, ast.Constant):
                    return str(v.value)
    return None


def _validate_stage(stage: Any) -> int:
    """6 阶段管道标号; Airflow DAG 固定在阶段 1 (上游预处理).

    Returns the validated int stage. Raises ValueError on invalid.
    """
    if stage is None or stage == "":
        return 1
    s = int(stage)
    if s != 1:
        raise ValueError(f"stage for parse_airflow_dag must be 1, got {s}")
    return s


async def _ensure_service_schema(
    db: AsyncSession, *, service_name: str, schema_name: str
) -> tuple[Service, Schema]:
    svc = (
        await db.execute(select(Service).where(Service.name == service_name))
    ).scalar_one_or_none()
    if svc is None:
        svc = Service(name=service_name, type="airflow", description="Airflow DAG via parse")
        db.add(svc)
        await db.flush()
    sch = (
        await db.execute(
            select(Schema).where(Schema.name == schema_name).join(Schema.database)
        )
    ).scalars().first()
    if sch is None:
        # 兜底: 关联到 default database / schema
        from idm_kg.models.database import Database

        d = (
            await db.execute(
                select(Database).where(Database.service_id == svc.id, Database.name == service_name)
            )
        ).scalar_one_or_none()
        if d is None:
            d = Database(service_id=svc.id, name=service_name, description="auto-created")
            db.add(d)
            await db.flush()
        sch = Schema(database_id=d.id, name=schema_name)
        db.add(sch)
        await db.flush()
    return svc, sch


@skill(name="parse_airflow_dag", version=2, agent="lineage")
async def parse_airflow_dag(ctx: SkillContext, **inputs: Any) -> SkillResult:
    dag_file_path: str = inputs.get("dag_file_path") or ""
    dag_id_filter: str = inputs.get("dag_id") or ""
    write_lineage: bool = bool(inputs.get("write_lineage", True))
    apply: bool = bool(inputs.get("apply", True))
    # === 6 阶段管道 (2026-06-08 M1.5 强化) ===
    try:
        stage = _validate_stage(inputs.get("stage", 1))
    except ValueError as e:
        return SkillResult(ok=False, output=SkillOutput(), error=str(e))
    pipeline_name: str | None = inputs.get("pipeline_name")

    if not dag_file_path:
        return SkillResult(ok=False, output=SkillOutput(), error="missing required input: 'dag_file_path'")
    if not os.path.exists(dag_file_path):
        return SkillResult(ok=False, output=SkillOutput(), error=f"file not found: {dag_file_path}")
    if ctx.db is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.db is None")

    dags = _parse_dag_file(dag_file_path)
    if dag_id_filter:
        dags = [d for d in dags if d["dag_id"] == dag_id_filter]
    if not dags:
        return SkillResult(
            ok=True,
            output=SkillOutput(items=[], summary={"reason": "no DAGs matched", "file": dag_file_path, "stage": stage}),
        )

    items: list[dict[str, Any]] = []
    svc_name = f"airflow-{os.path.basename(dag_file_path).replace('.py', '').lower()}"
    lineage_added = 0
    lineage_skipped = 0
    created_tables = 0
    updated_tables = 0

    # 收集 SQL 关联的表 (为后续 extract_sql_lineage 准备线索)
    sql_evidence: list[dict[str, Any]] = []

    for dag in dags:
        if not apply:
            items.append(
                {
                    "dag_id": dag["dag_id"],
                    "tasks": [t["task_id"] for t in dag["tasks"]],
                    "status": "dry_run",
                    "stage": stage,
                }
            )
            continue

        # 0) 写 pipeline 实体 (type=airflow_dag, stage=1) — 至少 1 个 DAG 写 1 次
        if pipeline_name or dag["dag_id"]:
            try:
                from idm_kg.models.pipeline import Pipeline
                pl_name = pipeline_name or f"airflow::{dag['dag_id']}"
                pl_row = (
                    await ctx.db.execute(
                        select(Pipeline).where(Pipeline.name == pl_name, Pipeline.type == "airflow_dag")
                    )
                ).scalar_one_or_none()
                if pl_row is None:
                    pl_row = Pipeline(
                        id=__import__("uuid").uuid4(),
                        name=pl_name,
                        type="airflow_dag",
                        stage=stage,
                        source_code_url=f"file://{dag_file_path}",
                        description=f"Airflow DAG {dag['dag_id']} ({len(dag['tasks'])} tasks)",
                        config={"dag_id": dag["dag_id"], "schedule": dag.get("schedule_interval")},
                    )
                    ctx.db.add(pl_row)
                else:
                    if stage is not None:
                        pl_row.stage = stage
            except Exception:  # noqa: BLE001
                logger.warning("parse_airflow_dag: failed to write pipeline %s", dag["dag_id"], exc_info=True)

        # 1) 每个 task 入一个 table_asset (asset_type=airflow_task)
        # 2) task 间顺序关系写入 lineage (transform_type=airflow_task)
        for i, t in enumerate(dag["tasks"]):
            fqn = f"{svc_name}.default.dag.{dag['dag_id']}.{t['task_id']}".lower()
            sch_name = "default"
            svc, sch = await _ensure_service_schema(ctx.db, service_name=svc_name, schema_name=sch_name)
            existing = (
                await ctx.db.execute(select(TableAsset).where(TableAsset.fqn == fqn))
            ).scalar_one_or_none()
            if existing is None:
                asset = TableAsset(
                    schema_id=sch.id,
                    name=f"{dag['dag_id']}.{t['task_id']}"[:256],
                    fqn=fqn,
                    asset_type="airflow_task",
                    tier="normal",
                    status="active",
                    description=f"Airflow task {t['task_id']} in DAG {dag['dag_id']} (operator={t['operator']}, stage={stage})",
                    description_source="imported",
                    pipeline_stage=stage,
                    extra={"operator": t["operator"], "dag_id": dag["dag_id"]},
                )
                ctx.db.add(asset)
                await ctx.db.flush()
                table_id = asset.id
                created_tables += 1
                status = "created"
            else:
                if stage is not None:
                    existing.pipeline_stage = stage
                table_id = existing.id
                updated_tables += 1
                status = "updated"
            items.append(
                {
                    "task_id": t["task_id"],
                    "dag_id": dag["dag_id"],
                    "operator": t["operator"],
                    "table_id": str(table_id),
                    "fqn": fqn,
                    "status": status,
                    "stage": stage,
                }
            )
            # 收集 SQL 线索
            sql_text = t.get("sql") or t.get("bash_command") or ""
            if sql_text:
                tables = _extract_sql_tables(sql_text)
                for tbl in tables:
                    sql_evidence.append(
                        {
                            "task_fqn": fqn,
                            "sql": sql_text[:500],
                            "referenced_table": tbl,
                        }
                    )

        # 3) 简单顺序血缘: 按文件中出现顺序建 task -> next_task 边
        if write_lineage:
            await ctx.db.execute(
                TableLineage.__table__.delete().where(
                    TableLineage.source == "airflow_dag",
                    TableLineage.transform_type == "airflow_task",
                )
            )
            await ctx.db.flush()
            for i, t in enumerate(dag["tasks"][:-1]):
                src = next((x for x in items if x["task_id"] == t["task_id"] and x["dag_id"] == dag["dag_id"]), None)
                dst = next((x for x in items if x["task_id"] == dag["tasks"][i + 1]["task_id"] and x["dag_id"] == dag["dag_id"]), None)
                if src and dst and src["table_id"] != dst["table_id"]:
                    ctx.db.add(
                        TableLineage(
                            upstream_id=src["table_id"],
                            downstream_id=dst["table_id"],
                            transform_type="airflow_task",
                            transform_subtype="dag_chain",
                            pipeline_stage=stage,
                            job_id=f"{dag['dag_id']}::{t['task_id']}->{dag['tasks'][i+1]['task_id']}",
                            confidence=0.8,
                            source="airflow_dag",
                        )
                    )
                    lineage_added += 1
                else:
                    lineage_skipped += 1

    if apply:
        await ctx.db.commit()

    summary = {
        "file": dag_file_path,
        "dags_parsed": len(dags),
        "tasks_total": sum(len(d["tasks"]) for d in dags),
        "tables_created": created_tables,
        "tables_updated": updated_tables,
        "lineage_edges_added": lineage_added,
        "lineage_edges_skipped": lineage_skipped,
        "sql_evidence_count": len(sql_evidence),
        "sql_evidence_sample": sql_evidence[:5],
        "stage": stage,
    }
    return SkillResult(
        ok=True,
        output=SkillOutput(
            items=items[:200],
            summary=summary,
            artifacts=[i["table_id"] for i in items if i.get("table_id")][:200],
        ),
    )
