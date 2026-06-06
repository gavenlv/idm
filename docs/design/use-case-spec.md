# IDM — Use Case YAML / JSON 规范

> **每个 use case = 一份声明式文件 = IDM 自动接管一个数据场景**
> 本文档给出完整字段、JSON Schema、3 套模板与 6 套真实场景示例

---

## 目录

- [1. 设计哲学](#1-设计哲学)
- [2. 顶层结构](#2-顶层结构)
- [3. 字段详解](#3-字段详解)
- [4. JSON Schema 校验](#4-json-schema-校验)
- [5. 模板 (Templates)](#5-模板-templates)
- [6. 真实场景示例](#6-真实场景示例)
- [7. 最佳实践](#7-最佳实践)
- [8. 演进与版本](#8-演进与版本)

---

## 1. 设计哲学

### 1.1 三个原则

| 原则 | 含义 |
| --- | --- |
| **单文件 = 单场景** | 一份 YAML 描述一个 use case (一张表 / 一组表 / 一条链路 / 一个 dashboard) |
| **人写 / LLM 写都 OK** | 字段语义清晰；LLM 可基于代码与文档自动生成 YAML |
| **GitOps 友好** | 文件入仓 = 启用 / 修改 / 暂停 |

### 1.2 文件位置

```text
idm/
└── use_cases/
    ├── production/
    │   ├── shop-orders-daily.yml
    │   ├── fct-sales.yml
    │   └── realtime-events.yml
    ├── staging/
    │   └── ...
    └── _templates/
        ├── clickhouse-only.yml
        ├── github-only.yml
        └── superset-only.yml
```

> 一个 use case 文件 = 一个被 IDM 长期监听、周期 / 事件触发的「任务契约」。

---

## 2. 顶层结构

```yaml
id: string                       # 唯一 ID, kebab-case
version: integer                 # 1
description: string              # 一句话说明
owners:                          # 谁拥有这个 use case
  - email|user|team

sources:                         # 通过哪些 MCP 拉数据
  - id, type, mcp, config, credentials_ref

context:                         # 业务上下文 (Agent 读)
  flow_diagram: string           # mermaid / 文字
  code_refs: []                  # GitHub / 本地路径
  docs_refs: []                  # 业务术语 / 设计文档
  glossary: []                   # 业务术语
  tags: []                       # 业务标签

analysis:                        # 需要 Agent 干什么
  - task, agent, schedule, params, depends_on, retry

deliverables:                    # 产出什么
  knowledge_graph: { ... }
  insights:    [ ... ]
  api_expose:  bool
  webhooks:    [ ... ]

guardrails:                      # 安全 / 限权
  llm:    { allow: bool, data_masking: bool }
  sql:    { readonly: true, max_rows: 1000 }
  notify: { channels: [...] }
```

---

## 3. 字段详解

### 3.1 顶层元信息

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | ✅ | 全局唯一，kebab-case |
| `version` | ✅ | 整数；变更时 +1 (IDM 用它做变更检测) |
| `description` | ✅ | 一句话；Agent 用它生成报告标题 |
| `owners` | ✅ | 至少 1 个 user email / team |

### 3.2 `sources[]` — 接入哪些系统

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | ✅ | 在 use case 内部唯一 |
| `type` | ✅ | `clickhouse` / `github` / `superset_export` / `airflow` / `flink` / `dbt` / `postgres` / `file` / `notion` / `slack` / `custom` |
| `mcp` | ✅ | MCP Server 名 (从 IDM MCP Registry 取) |
| `config` | ✅ | 连接配置 (host / repo / path / database...) |
| `credentials_ref` | ❌ | Secret Manager key (不要写明文) |
| `scope` | ❌ | 限定范围 (databases / paths / branches) |

### 3.3 `context` — 业务上下文

| 字段 | 说明 |
| --- | --- |
| `flow_diagram` | mermaid / 文字，描述数据从 A 到 B 的链路 |
| `code_refs[]` | `{ path, purpose, language }`，代码位置 + 用途 |
| `docs_refs[]` | 业务文档链接 (Notion / Lark / Markdown) |
| `glossary[]` | 业务术语定义 (Agent 用于 PII 推断 / 列名解释) |
| `tags[]` | 预打标签 (Agent 会复用) |

### 3.4 `analysis[]` — Agent 任务

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `task` | ✅ | `discover_assets` / `extract_lineage` / `generate_docs` / `suggest_owners` / `detect_anomalies` / `classify_pii` / `enrich_glossary` / `custom` |
| `agent` | ✅ | `schema` / `lineage` / `doc` / `owner` / `quality` / `pii` / `glossary` / `custom` |
| `schedule` | ❌ | cron 表达式；缺省 = 事件触发 |
| `params` | ❌ | 任务参数 |
| `depends_on` | ❌ | 任务 ID 列表 |
| `retry` | ❌ | `{ max: 3, backoff: exponential }` |
| `timeout` | ❌ | 秒 |

### 3.5 `deliverables` — 产出

```yaml
deliverables:
  knowledge_graph:                # 写回 KG
    entities: [table, column, dashboard, pipeline]
    relations: [upstream, downstream, references, owned_by, tagged, glossary]
  
  insights:                       # 推送
    - channel: slack
      target: "#data-stewards"
      trigger: [anomaly_detected, owner_missing, lineage_broken]
      template: "..."
    - channel: email
      target: ["{{ owner }}"]
      trigger: anomaly_detected
      frequency: immediate
  
  api_expose: true                # 暴露 REST / GraphQL
  webhooks:                       # 推给外部
    - url: https://...
      trigger: [doc_generated, lineage_changed]
      secret_ref: webhook-secret
```

### 3.6 `guardrails`

```yaml
guardrails:
  llm:
    allow: true                   # 是否允许 LLM 处理
    data_masking: true            # PII 自动 Mask
    max_tokens_per_call: 8000
  sql:
    readonly: true
    max_rows: 1000
    forbidden_functions: [url, file, input, s3, remote]
  notify:
    rate_limit: 10/min            # 防止轰炸
    quiet_hours: "22:00-08:00"
```

---

## 4. JSON Schema 校验

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "https://idm.io/schemas/use_case_v1.json",
  "title": "IDM Use Case",
  "type": "object",
  "required": ["id", "version", "description", "owners", "sources", "analysis"],
  "properties": {
    "id":         { "type": "string", "pattern": "^[a-z0-9-]+$" },
    "version":    { "type": "integer", "minimum": 1 },
    "description":{ "type": "string" },
    "owners":     { "type": "array", "items": { "type": "string" }, "minItems": 1 },
    "sources":    { "type": "array", "minItems": 1, "items": { "$ref": "#/$defs/source" } },
    "context":    { "$ref": "#/$defs/context" },
    "analysis":   { "type": "array", "minItems": 1, "items": { "$ref": "#/$defs/task" } },
    "deliverables":{ "$ref": "#/$defs/deliverables" },
    "guardrails": { "$ref": "#/$defs/guardrails" }
  },
  "$defs": {
    "source": {
      "type": "object",
      "required": ["id", "type", "mcp"],
      "properties": {
        "id":  { "type": "string" },
        "type":{ "enum": ["clickhouse","github","superset_export","airflow",
                          "flink","dbt","postgres","file","notion","slack","custom"] },
        "mcp": { "type": "string" },
        "config": { "type": "object" },
        "credentials_ref": { "type": "string" },
        "scope": { "type": "object" }
      }
    },
    "task": {
      "type": "object",
      "required": ["task", "agent"],
      "properties": {
        "task":  { "type": "string" },
        "agent": { "type": "string" },
        "schedule": { "type": "string" },
        "params":    { "type": "object" },
        "depends_on":{ "type": "array", "items": { "type": "string" } },
        "retry":     { "type": "object" },
        "timeout":   { "type": "integer" }
      }
    }
  }
}
```

---

## 5. 模板 (Templates)

### 5.1 Template: ClickHouse Only

```yaml
id: {{ use_case_id }}
version: 1
description: {{ 中文一句话 }}
owners: [alice@example.com]

sources:
  - id: ch-prod
    type: clickhouse
    mcp: clickhouse
    config:
      host: ch.example.com
      database: {{ db }}
    scope:
      include_tables: [{{ table_pattern }}]

analysis:
  - task: discover_assets
    agent: schema
    params: { profile_sample_size: 100 }
  - task: generate_docs
    agent: doc
  - task: detect_anomalies
    agent: quality
    schedule: "0 9 * * *"
```

### 5.2 Template: GitHub Only (代码即资产)

```yaml
id: {{ use_case_id }}
version: 1
description: 通过 GitHub 仓库的代码自动分析
owners: [team-data@example.com]

sources:
  - id: gh-repo
    type: github
    mcp: github
    config:
      repo: company/{{ repo }}
      branch: main

context:
  code_refs:
    - path: {{ code_path }}
      purpose: {{ 业务说明 }}

analysis:
  - task: discover_assets
    agent: schema
    params: { extract_from: [python_decorators, dbt_models, sql_files] }
  - task: extract_lineage
    agent: lineage
```

### 5.3 Template: Superset Export Only

```yaml
id: {{ use_case_id }}
version: 1
description: 通过 Superset 导出的 dashboard JSON 盘点所有 report
owners: [bi-team@example.com]

sources:
  - id: sp-export
    type: superset_export
    mcp: file
    config:
      path: gs://superset-exports/{{ yyyymm }}/


analysis:
  - task: discover_assets
    agent: schema
    params: { source_type: superset_chart }
  - task: extract_lineage
    agent: lineage
    params: { from: [superset_charts], resolve_sql: true }
  - task: classify_pii
    agent: pii
```

### 5.4 Template: 多源 (ClickHouse + GitHub + Superset)

见 [§6.1](#61-电商订单核心链-ch--gh--superset)

---

## 6. 真实场景示例

### 6.1 电商订单核心链 (CH + GH + Superset)

```yaml
id: shop-orders-daily
version: 1
description: 电商订单核心链：上游 Kafka → Airflow → ClickHouse 宽表 → Superset GMV Dashboard
owners:
  - alice@example.com
  - data-warehouse-team

sources:
  - id: ch-prod
    type: clickhouse
    mcp: clickhouse
    config:
      host: ch.example.com
      database: shop
    scope:
      include_tables: ["orders_daily", "orders_daily_v2"]
  - id: gh-warehouse
    type: github
    mcp: github
    config:
      repo: company/dwh
      branch: main
    scope:
      paths: ["dags/etl_orders*", "models/orders_*", "docs/orders*"]
  - id: sp-export
    type: superset_export
    mcp: file
    config:
      path: gs://superset-exports/2025-01/

context:
  flow_diagram: |
    ```mermaid
    flowchart LR
      A[Kafka: orders] --> B[Airflow: etl_orders]
      B --> C[ClickHouse: orders_daily]
      C --> D[Superset: GMV Dashboard]
      C --> E[ML: churn_v2]
    ```
  code_refs:
    - path: dags/etl_orders.py
      purpose: Airflow DAG, 从 Kafka 拉单
    - path: models/orders_daily.sql
      purpose: dbt Model, 字段清洗
    - path: docs/business/orders.md
      purpose: 业务字段定义
  glossary:
    - term: GMV
      definition: 成交总额, 含退款前
    - term: AOV
      definition: 客单价
  tags:
    - sales
    - critical
    - tier-1

analysis:
  - task: discover_assets
    agent: schema
    params:
      include_views: true
      profile_sample_size: 50
  - task: extract_lineage
    agent: lineage
    depends_on: [discover_assets]
    params:
      from: [dbt_manifest, airflow_dag, superset_charts]
      cross_source_join: true
  - task: generate_docs
    agent: doc
    depends_on: [discover_assets]
    params:
      tone: business
      language: zh
      min_confidence: 0.7
  - task: classify_pii
    agent: pii
    depends_on: [discover_assets]
  - task: suggest_owners
    agent: owner
    depends_on: [discover_assets]
    params:
      signals: [git_blame, dbt_meta, airflow_owner, query_log_top_users]
  - task: detect_anomalies
    agent: quality
    schedule: "0 9 * * *"
    depends_on: [discover_assets]
    params:
      baseline_days: 30
      sensitivity: medium

deliverables:
  knowledge_graph:
    entities: [table, column, dashboard, pipeline, tag, glossary_term]
    relations: [upstream, downstream, references, owned_by, tagged, glossary]
  insights:
    - channel: slack
      target: "#data-stewards"
      trigger: [anomaly_detected, lineage_broken, owner_missing]
      template: |
        :rotating_light: [{{ use_case.id }}] {{ event.title }}
        > {{ event.summary }}
        详情: {{ idm_url }}/use_cases/{{ use_case.id }}
    - channel: email
      target: ["{{ owner }}"]
      trigger: anomaly_detected
  api_expose: true

guardrails:
  llm:
    allow: true
    data_masking: true
  sql:
    readonly: true
    max_rows: 1000
  notify:
    rate_limit: 5/min
```

### 6.2 销售事实表 (PostgreSQL + dbt)

```yaml
id: fct-sales
version: 1
description: ERP 销售事实表, 来源 PG, dbt 构建, 业务报表核心
owners: [data-platform@example.com]

sources:
  - id: pg-erp
    type: postgres
    mcp: postgres
    config:
      host: pg-erp.example.com
      database: erp
    scope:
      schemas: [analytics]
  - id: gh-dbt
    type: github
    mcp: github
    config:
      repo: company/dbt-erp
      branch: main
    scope:
      paths: ["models/marts/sales/"]
  - id: dbt-manifest
    type: dbt
    mcp: file
    config:
      path: gs://dbt-artifacts/erp/

context:
  code_refs:
    - path: models/marts/sales/fct_sales.sql
      purpose: dbt 销售事实表
    - path: models/marts/sales/dim_customer.sql
      purpose: 客户维度

analysis:
  - task: discover_assets
    agent: schema
  - task: extract_lineage
    agent: lineage
    params: { from: [dbt_manifest, postgres_query_log] }
  - task: generate_docs
    agent: doc
  - task: detect_anomalies
    agent: quality
    schedule: "0 8 * * *"

deliverables:
  knowledge_graph: { entities: [table, column, model, test] }
  insights:
    - channel: slack
      target: "#bi-team"
```

### 6.3 实时事件流 (Flink + ClickHouse)

```yaml
id: realtime-events
version: 1
description: 实时用户行为事件流, Flink 处理, ClickHouse 落地
owners: [realtime-team@example.com]

sources:
  - id: flink-jobs
    type: flink
    mcp: flink
    config:
      rest_url: http://flink.example.com:8081
  - id: ch-realtime
    type: clickhouse
    mcp: clickhouse
    config:
      host: ch-rt.example.com
      database: events
  - id: gh-streaming
    type: github
    mcp: github
    config:
      repo: company/streaming-jobs
      branch: main
    scope:
      paths: ["jobs/events_*"]

context:
  flow_diagram: |
    Kafka(events) → Flink(etl, enrich) → ClickHouse(events_clean) → Redis / API

analysis:
  - task: discover_assets
    agent: schema
  - task: extract_lineage
    agent: lineage
    params: { from: [flink_plan, kafka_topic_schema] }
  - task: detect_anomalies
    agent: quality
    schedule: "*/15 * * * *"
    params: { sensitivity: high }   # 实时数据波动大, 调高敏感
```

### 6.4 BI Dashboard 盘点 (Superset export only)

```yaml
id: bi-dashboards
version: 1
description: 盘点 Superset 上所有 exported dashboard, 治理孤儿 dashboard
owners: [bi-team@example.com]

sources:
  - id: sp-export
    type: superset_export
    mcp: file
    config:
      path: gs://superset-exports/2025-01/

context:
  code_refs: []
  glossary: []

analysis:
  - task: discover_assets
    agent: schema
    params: { source_type: superset_chart }
  - task: extract_lineage
    agent: lineage
    params: { from: [superset_charts], resolve_sql: true, register_to_kg: true }
  - task: classify_pii
    agent: pii
  - task: suggest_owners
    agent: owner
    params: { signals: [superset_owner_field, last_editor] }

deliverables:
  insights:
    - channel: email
      target: ["{{ owner }}"]
      trigger: orphan_dashboard
      template: "Dashboard '{{ event.title }}' 已 30 天未访问, 建议归档"
```

### 6.5 ML 特征平台 (Feature Store + dbt + GH)

```yaml
id: ml-feature-store
version: 1
description: ML 特征平台, Feast + dbt, 支持 churn / recommender
owners: [ml-platform@example.com]

sources:
  - id: gh-feast
    type: github
    mcp: github
    config: { repo: company/feast-repo, branch: main }
  - id: dbt-ml
    type: dbt
    mcp: file
    config: { path: gs://dbt-artifacts/ml/ }
  - id: ch-features
    type: clickhouse
    mcp: clickhouse
    config: { host: ch.example.com, database: features }

analysis:
  - task: discover_assets
    agent: schema
    params: { source_type: feature_view }
  - task: extract_lineage
    agent: lineage
    params: { from: [feast_definitions, dbt_manifest, mlflow_runs] }
  - task: generate_docs
    agent: doc
```

### 6.6 仅代码即资产 (GitHub only)

```yaml
id: legacy-spark-jobs
version: 1
description: 旧版 Spark 任务盘点, 仅通过 GitHub 代码扫描
owners: [data-platform@example.com]

sources:
  - id: gh-spark
    type: github
    mcp: github
    config: { repo: company/legacy-spark, branch: main }
    scope:
      paths: ["src/main/scala/**"]

context:
  code_refs:
    - path: src/main/scala/etl/OrdersETL.scala
      purpose: 旧订单 ETL

analysis:
  - task: discover_assets
    agent: schema
    params: { extract_from: [scala_sparksql, python_decorators] }
  - task: extract_lineage
    agent: lineage
    params: { via: static_analysis }
  - task: generate_docs
    agent: doc
    params: { tone: technical }   # 老代码侧重技术
```

---

## 7. 最佳实践

### 7.1 命名
- `id` 用 `业务域-对象` 形式：`shop-orders-daily`, `bi-finance-dashboards`
- 避免太泛：`all-tables` ❌

### 7.2 拆分粒度
- **推荐**：1 use case = 1 主对象 + 它紧邻的上下游
- **避免**：1 use case = 整个数仓 (Agent context 容易爆)

### 7.3 `context.flow_diagram` 必填
- Agent 视觉化理解最快
- Mermaid / 文字都可，建议 Mermaid

### 7.4 `glossary` 越早写越好
- 列名 `gmv` 推断描述时直接引用

### 7.5 `owners` 永远必填
- AI 建议也要有人兜底

### 7.6 使用 _templates 起步
- 先 copy 模板 → 改 id / 描述 / sources → 提交
- IDM 自动开始工作

### 7.7 周期性 use case 用 `schedule`
- 实时性要求高 → `*/5 * * * *` (5 分钟)
- 质量监控 → `0 9 * * *` (每日 9 点)

---

## 8. 演进与版本

### 8.1 版本号规则
- `version` 整数递增
- 不兼容字段变更 → +1
- 兼容变更 (加可选字段) → 同一 version，commit message 说明

### 8.2 变更检测
- IDM 监听文件变更 (`fsnotify` / Git Webhook)
- 变更 → 重新加载 → 触发 `analyze`
- 失败 → 自动回滚到上一个 version + 通知

### 8.3 模板升级
- `_templates/` 是「样板」
- IDM 自身可基于历史 use case 提炼新模板 (Agent for Templates)

---

## 附录 A. MCP Server 清单 (IDM 内置支持)

| MCP Server | 类型 | 来源 |
| --- | --- | --- |
| `clickhouse` | clickhouse | 社区 (anthropic / 第三方) |
| `github` | github | @modelcontextprotocol/server-github |
| `gcs` | file | 自研 / 社区 |
| `file` | file | 自研 (本地 + GCS) |
| `postgres` | postgres | 社区 (crystaldba/postgres-mcp) |
| `airflow` | airflow | 自研 REST wrapper |
| `flink` | flink | 自研 REST wrapper |
| `superset` | superset | 自研 (基于 export) |
| `dbt` | dbt | 自研 (基于 manifest) |
| `notion` | notion | 官方 |
| `slack` | slack | 官方 |
| `lark` | lark | 官方 |
| `idm-self` | 自定义 | IDM 自身暴露 (供外部 Agent 查询 KG) |

## 附录 B. Agent ↔ Use Case 字段 映射表

| Use Case `analysis.task` | 默认 `agent` | 主要 MCP 调用 |
| --- | --- | --- |
| `discover_assets` | schema | `clickhouse.list_databases`, `github.search_code` |
| `extract_lineage` | lineage | `github.get_file_contents`, `file.read_object` |
| `generate_docs` | doc | `clickhouse.sample`, `notion.read_page` |
| `suggest_owners` | owner | `github.list_commits`, `airflow.get_dag` |
| `classify_pii` | pii | `clickhouse.sample` |
| `detect_anomalies` | quality | `clickhouse.run_select` (历史画像) |
| `enrich_glossary` | glossary | `notion.read_page`, `lark.read_doc` |
| `custom` | custom | 用户自定义 |

---

> 📌 **配套阅读**：[mcp-first-architecture.md](./mcp-first-architecture.md) · [walkthrough.md](./walkthrough.md)
