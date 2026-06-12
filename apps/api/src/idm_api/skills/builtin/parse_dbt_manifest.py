"""parse_dbt_manifest: 读 dbt manifest.json, 写入 table_assets / column_assets.

Inputs:
    manifest_path: str   本地 manifest.json 绝对路径 或 dbt_project 的 target/manifest.json
    project_name:  str   service 名 (默认 dbt-<project_name>, 落到 service 表)
    include_resource_types: list[str]  默认 ['model', 'seed', 'snapshot', 'source']
    dry_run: bool  仅解析, 不写库

Outputs (SkillOutput.items):
    [{table_id, fqn, resource_type, columns_added, depends_on_count, status}, ...]

写入:
    Service (dbt-{project}) -> Database -> Schema (取自 manifest.database/schema)
    -> TableAsset (asset_type=dbt_model 或 table for source)
    -> ColumnAsset

Lineage:
    depends_on.edges 暂不入 KG (asset_relationships 是 M2+), 返回到 items 里供后续 Skill 消费.

兼容:
    不依赖 dbt-core / dbt-manifest-parser, 仅 stdlib json.
    适配 dbt 1.5+ 的 manifest schema.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill
from idm_kg.models.column_asset import ColumnAsset
from idm_kg.models.database import Database
from idm_kg.models.schema import Schema
from idm_kg.models.service import Service
from idm_kg.models.table_asset import TableAsset
from idm_kg.models.table_lineage import TableLineage

logger = logging.getLogger(__name__)

DEFAULT_RESOURCE_TYPES = ["model", "seed", "snapshot", "source"]


def _slug(s: str) -> str:
    return s.lower().replace(" ", "-")


def _is_pii_column(name: str, description: str | None) -> bool:
    """dbt manifest 里若有 PII tag 我们认, 否则粗略按列名猜 (M1.5 兜底)."""
    if description and "pii" in (description or "").lower():
        return True
    name_l = name.lower()
    return any(k in name_l for k in ("email", "phone", "ssn", "id_card", "address"))


def _node_to_asset_payload(node: dict[str, Any], resource_type: str) -> dict[str, Any]:
    """把 dbt node 归一化到 (database, schema, name, description, columns, depends_on)."""
    if resource_type == "source":
        # sources 的结构略不同: identifier, schema
        return {
            "database": node.get("database") or node.get("source_name") or "unknown",
            "schema": node.get("schema") or node.get("source_name") or "default",
            "name": node.get("identifier") or node.get("name"),
            "description": node.get("description") or "",
            "columns": node.get("columns") or {},
            "depends_on": [],  # sources 没有 depends_on (上游在 warehouse)
            "asset_type": "table",  # source 在物理表, 不是 dbt model
            "raw_code": None,  # sources 没有 raw_code
        }
    return {
        "database": node.get("database") or "unknown",
        "schema": node.get("schema") or "default",
        "name": node.get("alias") or node.get("name"),
        "description": node.get("description") or "",
        "columns": node.get("columns") or {},
        "depends_on": [
            dep for dep in (node.get("depends_on") or {}).get("nodes", [])
            if not dep.startswith("test.")  # 过滤掉 test 节点
        ],
        "asset_type": "dbt_model",
        "raw_code": node.get("raw_code") or node.get("compiled_code") or None,
    }


def _preprocess_dbt_sql(sql: str) -> str:
    """把 dbt Jinja 模板 ({{ ref('x') }} / {{ source('x','y') }} / {% ... %}) 替换成纯 SQL.

    - {{ ref('x') }} -> x
    - {{ source('s', 't') }} -> t
    - {{ this }} -> _this_ (占位)
    - {{ var('x') }} -> 'x' (字面量)
    - {% if/for/endif/endfor/else %} -> 删除 tags, 保留 block 内部
    - {{ config(...) }} -> 删除

    返回的 SQL 可被 sqlglot 直接 parse.
    """
    if not sql:
        return sql
    import re
    # 1) {{ ref('x') }} / {{ ref("x") }} -> x
    sql = re.sub(r"\{\{\s*ref\(\s*['\"]?(\w+)['\"]?\s*\)\s*\}\}", r"\1", sql)
    # 2) {{ source('s', 't') }} -> t
    sql = re.sub(
        r"\{\{\s*source\(\s*['\"]?(\w+)['\"]?\s*,\s*['\"]?(\w+)['\"]?\s*\)\s*\}\}",
        r"\2",
        sql,
    )
    # 3) {{ this }} -> _this_
    sql = re.sub(r"\{\{\s*this\s*\}\}", "_this_", sql)
    # 4) {{ var('x') }} -> 'x'
    sql = re.sub(r"\{\{\s*var\(\s*['\"]?(\w+)['\"]?\s*\)\s*\}\}", r"'\1'", sql)
    # 5) {% ... %} tags (保留 block body)
    for tag in ("endfor", "endif", "endmacro", "else"):
        sql = re.sub(r"\{%\s*" + tag + r"\s*%\}", "", sql)
    sql = re.sub(r"\{%\s*for\s+\w+\s+in\s+[^%]+?%\}", "", sql)  # {% for x in y %}
    sql = re.sub(r"\{%\s*if\s+[^%]+?%\}", "", sql)  # {% if X %}
    # 6) {{ config(...) }} 多行
    sql = re.sub(r"\{\{\s*config\([^)]*\)\s*\}\}", "", sql, flags=re.DOTALL)
    # 7) 清理多余空行
    sql = re.sub(r"\n\s*\n+", "\n\n", sql)
    return sql.strip()


async def _ensure_service_db_schema(
    db: AsyncSession,
    *,
    service_name: str,
    database_name: str,
    schema_name: str,
) -> tuple[Service, Database, Schema]:
    svc = (
        await db.execute(select(Service).where(Service.name == service_name))
    ).scalar_one_or_none()
    if svc is None:
        svc = Service(name=service_name, type="dbt", description="dbt project via manifest.json")
        db.add(svc)
        await db.flush()

    d = (
        await db.execute(
            select(Database).where(Database.service_id == svc.id, Database.name == database_name)
        )
    ).scalar_one_or_none()
    if d is None:
        d = Database(service_id=svc.id, name=database_name)
        db.add(d)
        await db.flush()

    s = (
        await db.execute(
            select(Schema).where(Schema.database_id == d.id, Schema.name == schema_name)
        )
    ).scalar_one_or_none()
    if s is None:
        s = Schema(database_id=d.id, name=schema_name)
        db.add(s)
        await db.flush()
    return svc, d, s


@skill(name="parse_dbt_manifest", version=1, agent="schema")
async def parse_dbt_manifest(ctx: SkillContext, **inputs: Any) -> SkillResult:
    manifest_path: str = inputs.get("manifest_path") or ""
    project_name: str = inputs.get("project_name") or "dbt"
    include_resource_types: list[str] = inputs.get("include_resource_types") or DEFAULT_RESOURCE_TYPES
    dry_run: bool = bool(inputs.get("dry_run") or False)
    write_lineage: bool = bool(inputs.get("write_lineage", True))

    if not manifest_path:
        return SkillResult(ok=False, output=SkillOutput(), error="missing required input: 'manifest_path'")
    if not os.path.exists(manifest_path):
        return SkillResult(ok=False, output=SkillOutput(), error=f"manifest not found: {manifest_path}")
    if ctx.db is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.db is None")

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    ctx.log("manifest_loaded", path=manifest_path)

    nodes = manifest.get("nodes") or {}
    sources = manifest.get("sources") or {}

    # project 名优先取 metadata.project_name, 否则用入参
    proj = (manifest.get("metadata") or {}).get("project_name") or project_name
    service_name = f"dbt-{_slug(proj)}"

    items: list[dict[str, Any]] = []
    by_resource: dict[str, int] = {rt: 0 for rt in include_resource_types}
    total_deps = 0
    skipped = 0
    created = updated = 0
    lineage_edges_added = 0
    lineage_edges_skipped = 0

    # unique_id -> fqn 映射 (解析完所有节点后用于建血缘)
    fqn_by_uid: dict[str, str] = {}
    # 记录每个 entry 的 table_id, 方便后面回填
    fqn_to_table_id: dict[str, str] = {}

    # 1) 处理 sources
    if "source" in include_resource_types:
        for s_id, s_node in sources.items():
            payload = _node_to_asset_payload(s_node, "source")
            db_name, sc_name, name = payload["database"], payload["schema"], payload["name"]
            if not name:
                skipped += 1
                continue
            fqn = f"{service_name}.{db_name}.{sc_name}.{name}"
            entry = {
                "table_id": None,
                "fqn": fqn,
                "resource_type": "source",
                "columns_added": len(payload["columns"]),
                "depends_on_count": 0,
                "status": "dry_run" if dry_run else "pending",
            }
            if not dry_run:
                svc, db_obj, schema_obj = await _ensure_service_db_schema(
                    ctx.db,
                    service_name=service_name,
                    database_name=db_name,
                    schema_name=sc_name,
                )
                t, st = await _upsert_table(
                    ctx.db,
                    fqn=fqn,
                    schema_obj=schema_obj,
                    name=name,
                    description=payload["description"],
                    asset_type=payload["asset_type"],
                    columns=payload["columns"],
                )
                entry["table_id"] = str(t.id)
                entry["status"] = st
                if st == "created":
                    created += 1
                else:
                    updated += 1
            by_resource["source"] += 1
            items.append(entry)
            fqn_by_uid[s_id] = fqn
            if entry.get("table_id"):
                fqn_to_table_id[fqn] = entry["table_id"]

    # 2) 处理 models / seeds / snapshots
    for n_id, n_node in nodes.items():
        rt = n_node.get("resource_type")
        if rt not in include_resource_types:
            continue
        if rt in ("test", "analysis", "exposure", "operation", "macro", "documentation", "group"):
            continue
        payload = _node_to_asset_payload(n_node, rt)
        db_name, sc_name, name = payload["database"], payload["schema"], payload["name"]
        if not name:
            skipped += 1
            continue
        fqn = f"{service_name}.{db_name}.{sc_name}.{name}"
        deps = payload["depends_on"]
        total_deps += len(deps)
        entry = {
            "table_id": None,
            "fqn": fqn,
            "resource_type": rt,
            "columns_added": len(payload["columns"]),
            "depends_on_count": len(deps),
            "depends_on_sample": deps[:5],
            "status": "dry_run" if dry_run else "pending",
        }
        if not dry_run:
            svc, db_obj, schema_obj = await _ensure_service_db_schema(
                ctx.db,
                service_name=service_name,
                database_name=db_name,
                schema_name=sc_name,
            )
            t, st = await _upsert_table(
                ctx.db,
                fqn=fqn,
                schema_obj=schema_obj,
                name=name,
                description=payload["description"],
                asset_type=payload["asset_type"],
                columns=payload["columns"],
                extra={"dbt_unique_id": n_id, "dbt_resource_type": rt, "depends_on": deps},
            )
            entry["table_id"] = str(t.id)
            entry["status"] = st
            if st == "created":
                created += 1
            else:
                updated += 1
        by_resource[rt] = by_resource.get(rt, 0) + 1
        items.append(entry)
        fqn_by_uid[n_id] = fqn
        if entry.get("table_id"):
            fqn_to_table_id[fqn] = entry["table_id"]

    # 3) 写血缘 (depends_on 边)
    if write_lineage and not dry_run:
        # 清理已存在的 dbt_manifest 来源边 (幂等)
        await ctx.db.execute(
            TableLineage.__table__.delete().where(TableLineage.source == "dbt_manifest")
        )
        await ctx.db.flush()

        for entry in items:
            if entry["status"] in ("dry_run", "pending"):
                continue
            if entry["resource_type"] in ("source",):
                continue
            uid_full = next((u for u, q in fqn_by_uid.items() if q == entry["fqn"]), None)
            if uid_full is None:
                continue
            # 找原 node 的 depends_on
            n_node = nodes.get(uid_full) or {}
            deps = (n_node.get("depends_on") or {}).get("nodes", [])
            for dep_uid in deps:
                if dep_uid.startswith("test."):
                    continue
                up_fqn = fqn_by_uid.get(dep_uid)
                if not up_fqn:
                    lineage_edges_skipped += 1
                    continue
                up_id = fqn_to_table_id.get(up_fqn)
                if not up_id:
                    lineage_edges_skipped += 1
                    continue
                # 已有 (up, down, type=dbt_model) 边则跳过
                existing_edge = (
                    await ctx.db.execute(
                        select(TableLineage).where(
                            TableLineage.upstream_id == up_id,
                            TableLineage.downstream_id == entry["table_id"],
                            TableLineage.transform_type == "dbt_model",
                        )
                    )
                ).scalar_one_or_none()
                if existing_edge is not None:
                    # 更新 sql 字段 (以新 manifest 为准, 幂等)
                    raw_code = n_node.get("raw_code") or n_node.get("compiled_code") or None
                    if raw_code:
                        processed = _preprocess_dbt_sql(raw_code)
                        if processed and processed != existing_edge.sql:
                            existing_edge.sql = processed[:8190]
                    continue
                # 准备 sql 字段 (M2.5+ 让 infer_column_lineage 能 sqlglot 解析)
                raw_code = n_node.get("raw_code") or n_node.get("compiled_code") or None
                processed_sql = _preprocess_dbt_sql(raw_code)[:8190] if raw_code else None
                ctx.db.add(
                    TableLineage(
                        upstream_id=up_id,
                        downstream_id=entry["table_id"],
                        transform_type="dbt_model",
                        transform_subtype="ref",
                        job_id=uid_full,
                        component="dbt_model",
                        sql=processed_sql,
                        confidence=1.0,
                        source="dbt_manifest",
                        extra={"dbt_upstream_uid": dep_uid},
                    )
                )
                lineage_edges_added += 1

    if not dry_run:
        await ctx.db.commit()

    summary = {
        "manifest_path": manifest_path,
        "project": proj,
        "service": service_name,
        "dry_run": dry_run,
        "by_resource_type": by_resource,
        "tables_created": created,
        "tables_updated": updated,
        "skipped_no_name": skipped,
        "total_depends_on_edges": total_deps,
        "lineage_edges_added": lineage_edges_added,
        "lineage_edges_skipped": lineage_edges_skipped,
        "items_total": len(items),
    }
    ctx.log("done", **summary)
    return SkillResult(
        ok=True,
        output=SkillOutput(
            items=items[:200],  # 截断, 避免响应太大
            summary=summary,
            artifacts=[i["table_id"] for i in items if i.get("table_id")][:200],
        ),
    )


async def _upsert_table(
    db: AsyncSession,
    *,
    fqn: str,
    schema_obj: Schema,
    name: str,
    description: str,
    asset_type: str,
    columns: dict[str, dict],
    extra: dict | None = None,
) -> tuple[TableAsset, str]:
    existing = (await db.execute(select(TableAsset).where(TableAsset.fqn == fqn))).scalar_one_or_none()

    if existing is None:
        asset = TableAsset(
            schema_id=schema_obj.id,
            name=name,
            fqn=fqn,
            asset_type=asset_type,
            tier="normal",
            status="active",
            description=(description or "")[:4096] or None,
            description_source="imported" if description else None,
            column_count=len(columns),
            extra=extra or {"dbt_imported": True},
        )
        db.add(asset)
        await db.flush()
        table_id = asset.id
        status = "created"
    else:
        existing.column_count = len(columns)
        if description and not existing.description:
            existing.description = description[:4096]
            existing.description_source = "imported"
        if extra:
            existing.extra = {**(existing.extra or {}), **extra}
        table_id = existing.id
        status = "updated"
        # 清掉旧 columns, 重建
        cols_existing = (
            await db.execute(select(ColumnAsset).where(ColumnAsset.table_id == existing.id))
        ).scalars().all()
        for c in cols_existing:
            await db.delete(c)
        await db.flush()

    # 列
    for idx, (cname, cdef) in enumerate(columns.items()):
        ctype = (cdef or {}).get("data_type") or "Unknown"
        cdesc = (cdef or {}).get("description") or None
        is_pii = _is_pii_column(cname, cdesc)
        db.add(
            ColumnAsset(
                table_id=table_id,
                name=cname,
                ordinal=idx,
                data_type=ctype,
                nullable=True,
                is_primary_key=False,
                is_partition_key=False,
                description=cdesc,
                pii_class="other" if is_pii else "none",  # 待 PII Skill 进一步分类
                pii_source="dbt_tag" if is_pii else None,
                extra={"dbt_meta": (cdef or {}).get("meta") or {}},
            )
        )
    return await db.get(TableAsset, table_id), status
