# OpenLineage 兼容与互操作 (M2.5)

> **目标**: IDM 内部用 LLM 增强 (差异化), 对外能 emit / ingest OpenLineage 兼容事件 (互操作)。
>
> **范围**: 数据模型 (DDL) + Skill + API + 互操作 (Marquez / DataHub / Airflow OL plugin)。
>
> **参考标准**: [OpenLineage 1.0+ Spec](https://openlineage.io/) (Linux Foundation, LF AI & Data)

---

## 1. 为什么对齐 OpenLineage?

### 1.1 业界事实标准

| 项目 | 出身 | 状态 |
| --- | --- | --- |
| **OpenLineage** | Linux Foundation (LF AI & Data) | ⭐ 事实标准; Apache Airflow / Spark / dbt / Marquez 全支持 |
| **Marquez** | OpenLineage 参考实现 | OpenLineage 兼容后端; 我们的 export 目标之一 |
| **Apache Atlas** | Apache | 强 governance + classification, 概念多但偏重 |
| **DataHub** (LinkedIn) | LinkedIn 开源 | 关注 metadata, lineage 用 aspect model |
| **OpenMetadata** | 开源 | 已对齐 OpenLineage spec |
| **W3C PROV** | W3C | 提供 provenance 元模型 (可选借鉴) |
| **DataHub GMS** | LinkedIn | `DataJobInputOutput` aspect |

**结论**: **OpenLineage 是事实标准**, 我们应数据模型兼容, 但保留 LLM 增强的差异化护城河。

### 1.2 IDM 的差异化 (业界都没有)

| 能力 | OpenLineage / Atlas / DataHub | IDM M2.5 |
| --- | --- | --- |
| 静态血缘 (列→列, 100% 准确) | sqlglot / SQLLineage parser | ✅ sqlglot (已实现) |
| 跨命名约定列映射 | 同名匹配, 失败就丢 | ✅ LLM 推断 (`infer_column_lineage` LLM 兜底) |
| 黑盒模型 (MEX) 列推断 | 仅 IO 声明, 列级全丢 | ✅ LLM 读 `model_card.md` 推断输入输出列 |
| 血缘边的"自然语言" | 无 | ✅ 组件模板 + LLM (`infer_lineage_descriptions`) |
| 资产/列/边的描述 | 必须人工写 | ✅ 自动推断 (规则 70% + LLM 30%) |
| AI in the Loop | 无 | ✅ `ai_suggestion` 人工 confirm |
| 6 阶段管道统一 Schema | 各家各表 | ✅ GCS/Airflow/Flink/MEX/CH/Superset → 统一血缘图 |
| OpenLineage 兼容 export | 自身就是 | ✅ `emit_openlineage_event` skill + `/api/v1/lineage/openlineage/*` 端点 |
| OpenLineage 兼容 ingest | 自身就是 | ✅ `POST /api/v1/lineage/openlineage/ingest` |

**核心论点**: **业界 (OpenLineage 等) 只解决"what"和"how"** (静态事实); **IDM 在此基础上用 LLM 解决了"why"和"otherwise-unknown"** (业务上下文 / 黑盒 / 跨约定)。**我们做兼容, 也不丢差异化**。

---

## 2. 概念对齐表 (IDM ↔ OpenLineage)

| OpenLineage 概念 | OpenLineage 定义 | IDM 实体 / 字段 | 对齐方式 |
| --- | --- | --- | --- |
| **Dataset.namespace** | 资源所属命名空间 (e.g. `clickhouse://shop`) | `table_asset.ol_namespace` | ✅ M2.5 新增 (fallback: `service://database`) |
| **Dataset.name** | 资源名称 | `table_asset.name` | ✅ 一致 |
| **Dataset.facets** | 资源级 facet (description / schema / lifecycle 等) | `table_asset.extra` / `table_asset.description` | ✅ 已映射 (extra 是 facet 容器) |
| **Job.namespace** | Job 命名空间 (e.g. `airflow-prod`) | `lineage_event.job_namespace` | ✅ M2.5 新增 |
| **Job.name** | Job 名 (e.g. `etl_orders_daily`) | `lineage_event.job_name` / `pipeline.name` | ✅ 一致 |
| **Run.runId** | 单次 run 唯一 ID | `pipeline_run.external_id` (优先) / `pipeline_run.id` (fallback) | ✅ M2.5 |
| **RunEvent.eventType** | `START` / `RUNNING` / `COMPLETE` / `FAIL` / `ABORT` | `lineage_event.event_type` | ✅ M2.5 新增 |
| **RunEvent.eventTime** | ISO8601 时间戳 | `lineage_event.event_time` | ✅ M2.5 新增 |
| **RunEvent.producer** | 产生者标识 (e.g. `idm/0.4.0`) | `lineage_event.producer` | ✅ M2.5 新增 |
| **RunEvent.schemaURL** | JSON schema URL | `lineage_event.extra.schemaURL` | ✅ 写死为 OpenLineage 1.0 spec |
| **RunEvent.inputs** | 输入 Dataset 列表 | `lineage_event.inputs` (JSONB) | ✅ M2.5 新增 |
| **RunEvent.outputs** | 输出 Dataset 列表 | `lineage_event.outputs` (JSONB) | ✅ M2.5 新增 |
| **RunEvent.runFacets** | Run 级 facet (parent / processing_engine / ...) | `lineage_event.facets` (JSONB) | ✅ M2.5 新增 |
| **ColumnLineageDatasetFacet** | 列级血缘 facet | `lineage_event.inputs[i].facets.columnLineage` | ✅ 由 `column_lineage` 翻译 |
| **ColumnLineageFacet.fields.<col>.inputFields** | 上游列引用列表 | `column_lineage` 表 + `table_lineage` 边 | ✅ 多对一关系 |
| **ColumnLineageFacet.fields.<col>.transformations** | 转换操作列表 | `column_lineage.transformations` (JSONB) | ✅ M2.5 新增 (对齐 OL schema) |
| **TransformationType** | `DIRECT` / `TRANSFORMATION` (含 `subtype`) | `column_lineage.transform_type` (map) | ✅ M2.5 映射表 |

### 2.1 IDM transform_type ↔ OpenLineage transformation

| IDM `column_lineage.transform_type` | OpenLineage `transformation.type` | OpenLineage `transformation.subtype` |
| --- | --- | --- |
| `direct` | `DIRECT` | `null` |
| `passthrough` | `DIRECT` | `IDENTITY` |
| `rename` | `DIRECT` | `RENAME` |
| `cast` | `TRANSFORMATION` | `CAST` |
| `aggregation` | `TRANSFORMATION` | `AGGREGATION` |
| `expression` | `TRANSFORMATION` | `EXPRESSION` |
| `derivation` | `TRANSFORMATION` | `DERIVATION` |
| _(未识别)_ | `TRANSFORMATION` | _(大写 transform_type)_ |

映射代码: [emit_openlineage_event.py §`_TRANSFORM_TYPE_MAP`](../../apps/api/src/idm_api/skills/builtin/emit_openlineage_event.py)

### 2.2 不对齐 / 我们更细的地方

| 维度 | OpenLineage | IDM (M2.5) |
| --- | --- | --- |
| 描述来源追溯 | 无 (默认 producer) | `description_source` (`manual` / `ai_inferred` / `imported`) + `description_rationale` |
| AI in the Loop | 无 | `ai_suggestion` 表 (pending 状态, 人工 confirm) |
| PII 分类 | 无标准 (Atlas 有但非 OL) | `column_asset.pii_class` / `pii_confidence` / `pii_source` |
| 列级血缘 schema | `transformations` 是 object (subtype 仅 enum) | IDM 的 `transformations` 是 array of object (支持多步转换) |
| 6 阶段管道标号 | 无 | `pipeline_stage` (1..6) — IDM 业务模型 |

---

## 3. 数据模型补丁 (M2.5 — alembic 0006)

迁移文件: [migrations/versions/0006_openlineage_alignment.py](../../migrations/versions/0006_openlineage_alignment.py)

### 3.1 新表: `lineage_event` (append-only 审计)

```sql
CREATE TABLE lineage_event (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type      TEXT NOT NULL,    -- START | RUNNING | COMPLETE | FAIL | ABORT
    event_time      TIMESTAMPTZ NOT NULL DEFAULT now(),
    job_namespace   TEXT NOT NULL,    -- e.g. "airflow-prod"
    job_name        TEXT NOT NULL,    -- e.g. "etl_orders_daily"
    run_id          TEXT NOT NULL,    -- OpenLineage Run.runId
    inputs          JSONB NOT NULL DEFAULT '[]'::jsonb,
    outputs         JSONB NOT NULL DEFAULT '[]'::jsonb,
    facets          JSONB NOT NULL DEFAULT '{}'::jsonb,
    producer        TEXT,             -- e.g. "idm/0.4.0"
    source_skill    TEXT,             -- 哪个 IDM skill 触发的
    pipeline_run_id UUID REFERENCES pipeline_runs(id) ON DELETE SET NULL,
    extra           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_lineage_event_job  ON lineage_event(job_namespace, job_name, event_time);
CREATE INDEX ix_lineage_event_run  ON lineage_event(run_id);
CREATE INDEX ix_lineage_event_type ON lineage_event(event_type);
CREATE INDEX ix_lineage_event_time ON lineage_event(event_time);
```

### 3.2 `column_lineage` 新增 `transformations` JSONB

```sql
ALTER TABLE column_lineage
ADD COLUMN transformations JSONB NOT NULL DEFAULT '[]'::jsonb;
-- 结构 (对齐 OpenLineage ColumnLineageDatasetFacet):
-- [
--   {
--     "type": "DIRECT" | "TRANSFORMATION",
--     "subtype": "SUM" | "CAST" | "UPPER" | "RENAME" | "EXPRESSION" | ...,
--     "description": "聚合日订单风险分 (SUM(risk_score) GROUP BY day)",
--     "expression": "SUM(risk_score) GROUP BY day",
--     "masking": false
--   },
--   ...
-- ]
```

### 3.3 `table_assets` 新增 `ol_namespace`

```sql
ALTER TABLE table_assets ADD COLUMN ol_namespace TEXT;
CREATE INDEX ix_table_assets_ol_ns ON table_assets(ol_namespace);
-- e.g. "clickhouse://shop" / "gcs://company-raw" / "bigquery://project-id"
-- fallback: <service>://<database> (见 _default_ol_namespace in skill)
```

### 3.4 ORM 实体

- `LineageEvent` — [packages/kg/src/idm_kg/models/lineage_event.py](../../packages/kg/src/idm_kg/models/lineage_event.py)
- `ColumnLineage.transformations` — [packages/kg/src/idm_kg/models/column_lineage.py](../../packages/kg/src/idm_kg/models/column_lineage.py)
- `TableAsset.ol_namespace` — [packages/kg/src/idm_kg/models/table_asset.py](../../packages/kg/src/idm_kg/models/table_asset.py)

---

## 4. Skill: `emit_openlineage_event`

文件: [apps/api/src/idm_api/skills/builtin/emit_openlineage_event.py](../../apps/api/src/idm_api/skills/builtin/emit_openlineage_event.py)

### 4.1 输入

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `pipeline_run_id` | UUID (str) | 二选一 | 翻译该次 pipeline_run 涉及的所有 table_lineage |
| `job_namespace` + `job_name` + `run_id` | str | 二选一 | 自定义 Job/Run 标识 |
| `event_type` | str | 否 (默认 `COMPLETE`) | `START` / `RUNNING` / `COMPLETE` / `FAIL` / `ABORT` |
| `dry_run` | bool | 否 (默认 `False`) | True → 只算 JSON 不写库 |

### 4.2 输出

```python
SkillResult(
    ok=True,
    output=SkillOutput(
        items=[<ol_run_event_json>],   # 完整 OL RunEvent JSON
        summary={
            "events_emitted": 1,
            "inputs_count": N,
            "outputs_count": M,
            "column_lineage_facets": K,  # 含 ColumnLineageDatasetFacet 的 input 数
            "event_type": "COMPLETE",
            "job_namespace": "idm://shop-orders-mex-pipeline",
            "job_name": "etl_orders_daily",
            "run_id": "...",
            "ol_schema": "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent",
        },
        artifacts=[<lineage_event_id>],  # 写库时返回
    ),
)
```

### 4.3 OL RunEvent JSON 样例

```json
{
  "eventType": "COMPLETE",
  "eventTime": "2026-06-12T01:23:45.000+00:00",
  "producer": "idm/0.4.0 (idm-skill/emit_openlineage_event)",
  "schemaURL": "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent",
  "job": {
    "namespace": "idm://shop-orders-mex-pipeline",
    "name": "etl_orders_daily"
  },
  "run": {
    "runId": "scheduled__2026-06-12T01:00:00+00:00"
  },
  "inputs": [
    {
      "namespace": "gcs://company-raw",
      "name": "orders/2026/06/orders-20260608.csv",
      "facets": {
        "documentation": {
          "_producer": "idm/0.4.0",
          "description": "原始订单数据 (2026-06-08), 一行 = 一个订单"
        },
        "columnLineage": {
          "_producer": "idm/0.4.0",
          "fields": {
            "user_id": {
              "inputFields": [
                {"namespace": "gcs://company-raw", "name": "orders/...csv", "field": "user_id"}
              ],
              "transformations": [
                {"type": "TRANSFORMATION", "subtype": "CAST", "description": "...", "expression": "CAST(user_id AS UInt64)", "masking": false}
              ]
            }
          }
        }
      }
    }
  ],
  "outputs": [
    {
      "namespace": "clickhouse://shop",
      "name": "fct_orders_risk_daily",
      "facets": {
        "documentation": {
          "_producer": "idm/0.4.0",
          "description": "订单风险事实表 (天粒度)..."
        }
      }
    }
  ],
  "jobFacets": {},
  "runFacets": {
    "parent": {},
    "processing_engine": {"name": "idm", "version": "0.4.0"}
  }
}
```

---

## 5. API 端点 (M2.5)

文件: [apps/api/src/idm_api/routers/openlineage.py](../../apps/api/src/idm_api/routers/openlineage.py)

| Method | Path | 用途 |
| --- | --- | --- |
| `GET` | `/api/v1/lineage/openlineage/event/{event_id}` | 导出单个 lineage_event 为 OL RunEvent JSON |
| `GET` | `/api/v1/lineage/openlineage/events` | 列出最近 N 个 OL 事件 (支持 `job_namespace` / `job_name` / `run_id` 过滤) |
| `GET` | `/api/v1/lineage/openlineage/export/{pipeline_run_id}` | 翻译某 pipeline_run 为 OL RunEvent JSON (`dry_run=true`, 不写库) |
| `POST` | `/api/v1/lineage/openlineage/ingest` | 接受外部 OpenLineage 事件 (Marquez / DataHub / Airflow OL plugin 推送) |

### 5.1 完整端点速查

```bash
# 1. 列出最近 50 个 OL 事件
curl /api/v1/lineage/openlineage/events?limit=50

# 2. 按 job 过滤
curl "/api/v1/lineage/openlineage/events?job_namespace=airflow-prod&job_name=etl_orders_daily"

# 3. 翻译某次 pipeline_run 为 OL 事件 (不写库)
curl /api/v1/lineage/openlineage/export/{pipeline_run_id}?event_type=COMPLETE

# 4. 接受外部 OpenLineage 事件
curl -X POST /api/v1/lineage/openlineage/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "eventType": "COMPLETE",
    "eventTime": "2026-06-12T01:00:00Z",
    "producer": "airflow/2.7",
    "schemaURL": "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent",
    "job": {"namespace": "airflow-prod", "name": "etl_orders_daily"},
    "run": {"runId": "scheduled__2026-06-12T01:00:00+00:00"},
    "inputs": [...],
    "outputs": [...],
    "runFacets": {...}
  }'

# 5. 通过 skill 触发 (推荐 — 写 lineage_event 表)
curl -X POST /api/v1/skills/run \
  -H "Content-Type: application/json" \
  -d '{
    "skill": "emit_openlineage_event",
    "inputs": {
      "pipeline_run_id": "uuid...",
      "event_type": "COMPLETE"
    }
  }'
```

---

## 6. 互操作场景 (M2.5 落地)

### 6.1 推送到 Marquez (标准用法)

```bash
# Marquez 部署 (e.g. http://marquez:5000)
# 1) IDM 端 export OL JSON
curl /api/v1/lineage/openlineage/export/{pipeline_run_id} > /tmp/ol_event.json

# 2) 推到 Marquez Lineage API
curl -X POST http://marquez:5000/api/v1/lineage \
  -H "Content-Type: application/json" \
  -d @/tmp/ol_event.json

# Marquez 自动建 namespace/job/dataset, 持久化血缘图
```

### 6.2 Airflow OpenLineage plugin 推过来 (反方向)

```python
# airflow 配置 (env: AIRFLOW__OPENLINEAGE__TRANSPORT=json)
# AIRFLOW__OPENLINEAGE__TRANSPORT__ENDPOINT=http://idm:8080/api/v1/lineage/openlineage/ingest
# idm /api/v1/lineage/openlineage/ingest 接受, 写 lineage_event (审计)
# ⚠️ 当前实现: 仅审计, 不反向同步到 table_lineage / column_lineage (那需要 IDM-side 映射 skill, M2.6 规划)
```

### 6.3 DataHub 集成 (M2.6+)

- DataHub GMS 接受 OpenLineage 事件 (通过 `datahub-actions` OL source)
- IDM 可同时推 DataHub + Marquez
- 详见 [DataHub OpenLineage integration](https://datahubproject.io/docs/metadata-integration/openlineage/)

---

## 7. 评估指标 (M2.5 Eval)

| 维度 | 指标 | 目标 |
| --- | --- | --- |
| **OpenLineage schema 兼容** | export JSON 通过 [OpenLineage schema validator](https://github.com/OpenLineage/OpenLineage/tree/main/spec) | 100% |
| **双向可推** | IDM → Marquez 推送成功 | ✅ |
| **双向可收** | Airflow OL plugin → IDM ingest 成功 | ✅ |
| **Field 覆盖** | ColumnLineageDatasetFacet.fields 含 % 列 | ≥ 60% |
| **Transformation 准确** | 抽样 review transformations 是否正确反映 SQL | ≥ 80% |
| **event_type 一致** | pipeline_run 成功 → event_type=COMPLETE | 100% |

---

## 8. 已落地状态 (M2.5 — 2026-06-12)

- [x] **数据模型**: 迁移 `0006_openlineage_alignment.py` (DDL + 索引)
- [x] **ORM 实体**: `LineageEvent` / `column_lineage.transformations` / `table_assets.ol_namespace`
- [x] **Skill**: `emit_openlineage_event` v1 (含 transform_type 映射 + ColumnLineageFacet 构建)
- [x] **Router**: `/api/v1/lineage/openlineage/{event,events,export,ingest}` (4 个端点)
- [x] **Pydantic**: `OpenLineageEventRead` schema
- [x] **互操作**: 可推 Marquez / 可收 Airflow OL plugin
- [ ] **IDM-side 反向映射** (OL ingest → table_lineage / column_lineage): M2.6 规划
- [ ] **DataHub GMS 集成**: M2.6 规划
- [ ] **W3C PROV 兼容**: 可选, M3 评估

---

## 9. 不破坏 M2.x 的保证 (兼容性)

- **不删除任何现有字段**
- **不重命名任何现有字段**
- **不修改任何现有 Skill 行为**
- 仅 **新增 1 表 + 2 字段**, 都是 nullable / 有 default
- M2.x 4 个 skill (`infer_column_descriptions` / `infer_column_lineage` / `lineage_to_column` / `infer_lineage_descriptions`) **行为完全不变**
- 老 M2.x 客户端 (M2.x API) 无需任何改动, 仍能正常工作

---

## 10. 后续路线 (M2.6+)

| 版本 | 内容 | 优先级 |
| --- | --- | --- |
| **M2.6** | IDM-side 反向映射 (OL ingest → table_lineage / column_lineage) | P1 |
| **M2.6** | DataHub GMS 集成 (同时推 DataHub + Marquez) | P1 |
| **M2.7** | 实时 OpenLineage event 流 (WebSocket / Kafka) | P2 |
| **M2.7** | OL facet 扩展 (dataQuality / dataSource / schema 变更) | P2 |
| **M3** | W3C PROV 兼容 (审计 / provenance 查询) | P3 |
| **M3** | OpenLineage Spark Integration (SparkListener 捕获运行时血缘) | P3 |

---

## 11. 关联文档

- 数据模型: [data-model.md §2 ER 模型](./data-model.md#2-关系型-er-模型-postgresql) / §7 (M2.x 语义增强, 已迁移至 [ai-driven-design.md §11](./ai-driven-design.md#11-列级血缘与智能描述推断-column-level-lineage--smart-description-inference--m2x))
- Skill 设计: [skills-design.md §11 M2.x 新增](./skills-design.md#111-m2x-新增-skill-详解-语义增强--列级血缘) (已迁移)
- Agent 视角: [AGENT_INSTRUCTIONS.md §16.7](../AGENT_INSTRUCTIONS.md#167-语义增强--列级血缘子系统-semantic-enrichment--column-level-lineage-m2x) (引用本文为权威)
- 6 阶段管道: [data-pipeline-lineage.md §4.3](./data-pipeline-lineage.md#43-m2x-新增-列级血缘--语义描述-semantic-enrichment--已迁移) (已迁移)

---

## 12. 参考资料

- [OpenLineage Spec](https://openlineage.io/spec/) — 1.0+ RunEvent / Dataset / Job / Run 定义
- [OpenLineage ColumnLineageDatasetFacet](https://openlineage.io/spec/facets/DatasetFacets/column_lineage_facet/) — 列级血缘 schema
- [Marquez 项目](https://marquezproject.ai/) — OpenLineage 参考实现
- [Apache Atlas](https://atlas.apache.org/) — Classification / PII 传播概念借鉴
- [DataHub](https://datahubproject.io/) — LinkedIn 开源, DataJob 概念
- [W3C PROV-DM](https://www.w3.org/TR/prov-dm/) — Provenance 标准 (可选借鉴)
