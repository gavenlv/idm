"""parse_superset_dashboard Skill (M1 S1.8).

通过 Superset MCP (REST API) 拉取 dashboards / charts / datasets,
把它们作为 IDM 资产写入知识图谱, 并建立 chart -> dataset -> CH 表 的 lineage.

约定资产:
- dashboard (id 形如 <superset>.<dashboard_id>)  -> asset_type = superset_dashboard
- chart     (id 形如 <superset>.<chart_id>)      -> asset_type = superset_chart
- dataset   (id 形如 <superset>.<dataset_id>)    -> asset_type = superset_dataset
- ClickHouse 表通过 (database_name, schema, table_name) 在 KG 中查找, 建
  chart -> dataset -> table 两条 lineage edge.

输入 (Skill inputs):
- dashboard_ids:  list[int]   指定要导入的 dashboard id, 为空 = 全部
- dashboard_limit: int        列出 dashboard 时的上限 (默认 50)
- include_charts:  bool       是否同步导入 chart 资产 (默认 True)
- include_datasets: bool      是否同步导入 dataset 资产 (默认 True)
- service_name:   str         资产 service 字段 (默认 'superset')
- dry_run:        bool        只读不写 (走 dry_run 通道)

输出 summary:
{
  "superset_reachable": bool,
  "dashboards_seen": int,
  "charts_seen": int,
  "datasets_seen": int,
  "dashboard_assets": int,   # 新建/更新
  "chart_assets": int,
  "dataset_assets": int,
  "lineage_edges_added": int,
  "errors": list[str],
}
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from idm_api.skills.registry import SkillContext, SkillOutput, SkillResult, skill
from idm_api.skills.mcp import get_superset_mcp
from idm_kg.models.database import Database
from idm_kg.models.pipeline import Pipeline
from idm_kg.models.schema import Schema
from idm_kg.models.service import Service
from idm_kg.models.table_asset import TableAsset
from idm_kg.models.table_lineage import TableLineage


# === helper: 找/建资产 (async) ===

async def _ensure_table_asset(
    db: Any,
    *,
    fqn: str,
    name: str,
    schema_id: str,
    asset_type: str = "clickhouse_table",
    description: str | None = None,
    extra: dict[str, Any] | None = None,
    pipeline_stage: int | None = None,
) -> str:
    """upsert TableAsset by fqn, 返回 id."""
    values = dict(
        fqn=fqn, name=name, schema_id=schema_id,
        asset_type=asset_type, description=description, extra=extra or {},
    )
    if pipeline_stage is not None:
        values["pipeline_stage"] = pipeline_stage
    stmt = pg_insert(TableAsset).values(**values).on_conflict_do_update(
        index_elements=[TableAsset.fqn],
        set_={"name": name, "asset_type": asset_type,
              "description": description or TableAsset.description,
              "extra": extra or TableAsset.extra,
              "schema_id": schema_id,
              "pipeline_stage": pipeline_stage if pipeline_stage is not None else TableAsset.pipeline_stage,
              "updated_at": TableAsset.__table__.c.updated_at},
    )
    await db.execute(stmt)
    row = (await db.execute(select(TableAsset).where(TableAsset.fqn == fqn))).scalar_one()
    return row.id


async def _ensure_schema(
    db: Any,
    *,
    schema_fqn: str,
    name: str,
    service_id: str,
) -> str:
    """确保 service -> database -> schema 链路存在, 返回 schema.id.

    schema_fqn 是形如 <service>._superset 的逻辑命名, 仅用于日志;
    实际写入: service=<service_id 字面>, database=schema_fqn, schema=name.
    """
    # 1) Service (按 name 找, 不存在就建)
    svc = (await db.execute(
        select(Service).where(Service.name == service_id)
    )).scalar_one_or_none()
    if svc is None:
        svc = Service(name=service_id, type="superset",
                      description="Superset via MCP")
        db.add(svc)
        await db.flush()

    # 2) Database
    db_row = (await db.execute(
        select(Database).where(Database.service_id == svc.id, Database.name == schema_fqn)
    )).scalar_one_or_none()
    if db_row is None:
        db_row = Database(service_id=svc.id, name=schema_fqn,
                          description=f"Superset 逻辑库 {schema_fqn}")
        db.add(db_row)
        await db.flush()

    # 3) Schema
    s = (await db.execute(
        select(Schema).where(Schema.database_id == db_row.id, Schema.name == name)
    )).scalar_one_or_none()
    if s is None:
        s = Schema(database_id=db_row.id, name=name,
                   description=f"Superset assets namespace {name}")
        db.add(s)
        await db.flush()
    return s.id


def _parse_dataset_table_ref(ds: dict[str, Any]) -> dict[str, str] | None:
    """从 Superset dataset 详情里解出 (database, schema, table)."""
    if not ds:
        return None
    db_obj = ds.get("database") or {}
    if isinstance(db_obj, dict):
        db_name = (db_obj.get("database_name") or db_obj.get("name") or "").strip()
    else:
        db_name = ""
    schema = (ds.get("schema") or "").strip()
    table = (ds.get("table_name") or "").strip()
    if not (db_name and table):
        return None
    return {"database": db_name, "schema": schema, "table": table}


def _ch_service_name(db_name: str) -> str:
    """Superset database name -> IDM service (ClickHouse) name.

    约定: ch-<db_name> (与 collect_clickhouse 写入的 service 一致)."""
    return f"ch-{db_name}"


def _validate_stage(stage):
    """6 阶段管道标号; Superset 固定在阶段 6 (Report 消费)."""
    if stage is None or stage == "":
        return 6
    s = int(stage)
    if s != 6:
        raise ValueError(f"stage for parse_superset_dashboard must be 6, got {s}")
    return s


@skill(name="parse_superset_dashboard", version=2, agent="lineage")
async def run(ctx: SkillContext, **inputs: Any) -> SkillResult:
    """主入口."""
    dashboard_ids: list[int] = list(inputs.get("dashboard_ids") or [])
    dashboard_limit: int = int(inputs.get("dashboard_limit") or 50)
    include_charts: bool = bool(inputs.get("include_charts", True))
    include_datasets: bool = bool(inputs.get("include_datasets", True))
    service_name: str = str(inputs.get("service_name") or "superset")
    dry_run: bool = bool(inputs.get("dry_run", False))
    # === 6 阶段管道 (2026-06-08 M1.5 强化) ===
    try:
        stage = _validate_stage(inputs.get("stage", 6))
    except ValueError as e:
        return SkillResult(ok=False, output=SkillOutput(), error=str(e))
    pipeline_name: str | None = inputs.get("pipeline_name")

    summary: dict[str, Any] = {
        "superset_reachable": False,
        "dashboards_seen": 0,
        "charts_seen": 0,
        "datasets_seen": 0,
        "dashboard_assets": 0,
        "chart_assets": 0,
        "dataset_assets": 0,
        "lineage_edges_added": 0,
        "stage": stage,
        "errors": [],
    }

    ss = get_superset_mcp()
    health = await ss.health()
    summary["superset_reachable"] = health.get("status") == "ok"
    if not summary["superset_reachable"]:
        summary["errors"].append(f"superset unreachable: {health}")
        return SkillResult(ok=False, output=SkillOutput(summary=summary), error="superset_unreachable")

    # 1) 拉 dashboard 列表
    try:
        if dashboard_ids:
            dashboards = []
            for did in dashboard_ids:
                d = await ss.get_dashboard(int(did))
                if d:
                    dashboards.append(d)
        else:
            dashboards = await ss.list_dashboards(limit=dashboard_limit)
    except Exception as e:  # noqa: BLE001
        summary["errors"].append(f"list_dashboards failed: {e}")
        return SkillResult(ok=False, output=SkillOutput(summary=summary), error=str(e)[:200])

    summary["dashboards_seen"] = len(dashboards)

    # 2) 收集 chart ids (从 dashboard.json_metadata.slices 提取), 并去重
    chart_ids: set[int] = set()
    for d in dashboards:
        meta = d.get("json_metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:  # noqa: BLE001
                meta = {}
        if not isinstance(meta, dict):
            meta = {}
        for cid in meta.get("chartId") or []:
            try:
                chart_ids.add(int(cid))
            except Exception:  # noqa: BLE001
                pass
        for s in d.get("slices") or []:
            if isinstance(s, dict) and s.get("id"):
                try:
                    chart_ids.add(int(s["id"]))
                except Exception:  # noqa: BLE001
                    pass

    # 3) 拉 chart 详情 (获得 dataset 引用)
    charts: list[dict[str, Any]] = []
    dataset_ids: set[int] = set()
    if include_charts and chart_ids:
        for cid in chart_ids:
            try:
                c = await ss.get_chart(cid)
                if c:
                    charts.append(c)
                    ds_id = (c.get("datasource_id") or 0)
                    ds_type = c.get("datasource_type") or ""
                    if ds_type == "dataset" and ds_id:
                        dataset_ids.add(int(ds_id))
            except Exception as e:  # noqa: BLE001
                summary["errors"].append(f"get_chart({cid}) failed: {e}")
    summary["charts_seen"] = len(charts)

    # 4) 拉 dataset 详情 (获得 database / schema / table)
    datasets: list[dict[str, Any]] = []
    if include_datasets and dataset_ids:
        for did in dataset_ids:
            try:
                ds = await ss.get_dataset(did)
                if ds:
                    datasets.append(ds)
            except Exception as e:  # noqa: BLE001
                summary["errors"].append(f"get_dataset({did}) failed: {e}")
    summary["datasets_seen"] = len(datasets)

    if dry_run or ctx.db is None:
        return SkillResult(ok=True, output=SkillOutput(summary=summary))

    db = ctx.db

    # 5a) dataset 资产 (同时建到对应 CH table 的 lineage)
    dataset_id_set = {d.get("id") for d in datasets if d.get("id") is not None}
    schema_id = await _ensure_schema(
        db, schema_fqn=f"{service_name}._superset", name="_superset", service_id=service_name,
    )

    for ds in datasets:
        did = ds.get("id")
        ref = _parse_dataset_table_ref(ds)
        ds_fqn = f"{service_name}.ds.{did}"
        ds_desc = ds.get("description") or f"Superset dataset #{did}"
        extra = {
            "superset_url": f"{ss._base}/superset/dataset/{did}",
            "database": ref["database"] if ref else None,
            "schema": ref["schema"] if ref else None,
            "table": ref["table"] if ref else None,
        }
        ds_asset_id = await _ensure_table_asset(
            db, fqn=ds_fqn, name=f"dataset_{did}", schema_id=schema_id,
            asset_type="superset_dataset", description=ds_desc, extra=extra,
            pipeline_stage=stage,
        )
        summary["dataset_assets"] += 1

        # 关联到 CH 表 (若存在) - 兼容多种 service 命名 + dbt 视图 + schema=default 兜底
        # FQN: <service>.<database>.<schema>.<table>
        if ref:
            db_name = ref["database"]
            sch = ref["schema"] or "default"
            tbl = ref["table"]
            # 兼容 dbt 项目 database 与 CH 物理 database 命名差异
            db_aliases = {db_name, "shop", "shop_dw"}
            services = ["ch-shop_dw", "clickhouse-prod", "dbt-shop_dw"]
            candidates: list[str] = []
            for svc in services:
                for dba in db_aliases:
                    for sn in (sch, "default"):
                        cand = f"{svc}.{dba}.{sn}.{tbl}"
                        if cand not in candidates:
                            candidates.append(cand)
            row = None
            for cand in candidates:
                row = (await db.execute(
                    select(TableAsset).where(TableAsset.fqn == cand)
                )).scalar_one_or_none()
                if row:
                    break
            if row:
                # 建 dataset -> table lineage (Superset dataset 是上游 view, CH 表是底层)
                stmt = pg_insert(TableLineage).values(
                    upstream_id=ds_asset_id, downstream_id=row.id,
                    transform_type="superset_dataset", source="superset_dataset",
                    transform_subtype="dataset_to_table",
                    pipeline_stage=stage,
                    confidence=0.95,
                ).on_conflict_do_nothing(index_elements=[
                    TableLineage.upstream_id, TableLineage.downstream_id, TableLineage.transform_type,
                ])
                res = await db.execute(stmt)
                if res.rowcount and res.rowcount > 0:
                    summary["lineage_edges_added"] += res.rowcount

    # 5b) chart 资产 (chart -> dataset lineage)
    for c in charts:
        cid = c.get("id")
        ds_id = int(c.get("datasource_id") or 0) if c.get("datasource_type") == "dataset" else 0
        c_fqn = f"{service_name}.chart.{cid}"
        c_desc = c.get("description") or c.get("slice_name") or f"Superset chart #{cid}"
        extra = {
            "superset_url": f"{ss._base}/explore/?slice_id={cid}",
            "viz_type": c.get("viz_type"),
            "datasource_id": ds_id or None,
            "datasource_type": c.get("datasource_type"),
            "dashboard_ids": list(dashboard_ids) if dashboard_ids else [],
        }
        c_asset_id = await _ensure_table_asset(
            db, fqn=c_fqn, name=str(c.get("slice_name") or f"chart_{cid}"),
            schema_id=schema_id, asset_type="superset_chart", description=c_desc, extra=extra,
        )
        summary["chart_assets"] += 1

        # chart -> dataset lineage
        if ds_id and ds_id in dataset_id_set:
            ds_asset = (await db.execute(
                select(TableAsset).where(TableAsset.fqn == f"{service_name}.ds.{ds_id}")
            )).scalar_one_or_none()
            if ds_asset:
                stmt = pg_insert(TableLineage).values(
                    upstream_id=c_asset_id, downstream_id=ds_asset.id,
                    transform_type="superset_chart", source="superset_chart",
                    confidence=0.95,
                ).on_conflict_do_nothing(index_elements=[
                    TableLineage.upstream_id, TableLineage.downstream_id, TableLineage.transform_type,
                ])
                res = await db.execute(stmt)
                if res.rowcount and res.rowcount > 0:
                    summary["lineage_edges_added"] += res.rowcount

    # 5c) dashboard 资产 (并把所含 chart 列入 extra.charts)
    for d in dashboards:
        did = d.get("id")
        d_fqn = f"{service_name}.dashboard.{did}"
        d_title = d.get("dashboard_title") or f"dashboard_{did}"
        d_desc = d.get("description") or d_title
        meta = d.get("json_metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:  # noqa: BLE001
                meta = {}
        chart_in_dash = list(meta.get("chartId") or []) if isinstance(meta, dict) else []
        extra = {
            "superset_url": f"{ss._base}/superset/dashboard/{did}/",
            "slug": d.get("slug"),
            "owner_id": d.get("owner_id"),
            "published": d.get("published"),
            "chart_ids": chart_in_dash,
        }
        await _ensure_table_asset(
            db, fqn=d_fqn, name=d_title, schema_id=schema_id,
            asset_type="superset_dashboard", description=d_desc, extra=extra,
            pipeline_stage=stage,
        )
        summary["dashboard_assets"] += 1

    # 0) 写 pipeline 实体 (type=superset_refresh, stage=6) — 至少 1 个
    try:
        pl_name = pipeline_name or f"superset::{service_name}::refresh"
        pl_row = (
            await db.execute(
                select(Pipeline).where(Pipeline.name == pl_name, Pipeline.type == "superset_refresh")
            )
        ).scalar_one_or_none()
        if pl_row is None:
            pl_row = Pipeline(
                id=__import__("uuid").uuid4(),
                name=pl_name,
                type="superset_refresh",
                stage=stage,
                source_code_url=f"{ss._base}/superset/dashboard/list/" if hasattr(ss, "_base") else None,
                description=f"Superset dashboards refresh ({len(dashboards)} dashboards)",
                config={"dashboards": len(dashboards), "service": service_name},
            )
            db.add(pl_row)
        else:
            if stage is not None:
                pl_row.stage = stage
    except Exception:  # noqa: BLE001
        pass

    await db.commit()

    return SkillResult(ok=True, output=SkillOutput(summary=summary))
