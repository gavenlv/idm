# IDM (Intelligent Data Mesh) — Monorepo Root

> **AI-driven, MCP-first, Zero-Touch data management platform.**
> 业务系统零改动; 业务 1 份 YAML; Agent 接管治理全过程。

## 文档入口 (📌 必读)

- [AGENT_INSTRUCTIONS.md](./docs/AGENT_INSTRUCTIONS.md) — 宪法级摘要 (5 分钟读完)
- [docs/design/architecture.md](./docs/design/architecture.md) — 总体架构
- [docs/design/ai-driven-design.md](./docs/design/ai-driven-design.md) — AI 驱动核心
- [docs/design/mcp-first-architecture.md](./docs/design/mcp-first-architecture.md) — MCP-First
- [docs/design/use-case-spec.md](./docs/design/use-case-spec.md) — Use Case YAML
- [docs/design/roadmap.md](./docs/design/roadmap.md) — 5 季度里程碑

## Monorepo 结构 (M1 起点)

```
idm/
├── apps/
│   ├── api/              # FastAPI idm-api  (CRUD / GraphQL / 健康)
│   ├── agent/            # LangGraph Planner + 9 Specialist  (M2)
│   ├── web/              # React + Vite + ag-grid Console  (M1 S1.3)
│   └── mcp-servers/      # MCP server 集合  (M2 起步)
├── packages/
│   ├── kg/               # SQLAlchemy 领域模型 / AGE / pgvector client
│   ├── skills/           # Skill spec + runner + validator  (M2)
│   ├── llm/              # LiteLLM 包装 / Langfuse 集成  (M2)
│   └── observability/    # OpenTelemetry / 审计 / 指标
├── deploy/
│   ├── helm/             # Helm charts (GKE)
│   ├── docker/           # Dockerfile
│   └── argocd/           # GitOps Application manifests
├── migrations/           # Alembic
├── use_cases/            # Use Case YAML 仓库
│   ├── _templates/
│   └── shop-orders-daily.yml
├── docs/                 # 设计文档 (已存在)
├── pyproject.toml        # uv workspace
├── Makefile
└── README.md
```

## 快速开始 (M1 S1.1)

```bash
# 1. 准备环境
py -m pip install uv
uv sync

# 2. 启动 Postgres (docker 本地; 生产用 CloudSQL)
docker compose -f deploy/docker/compose.dev.yml up -d

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env, 填 DATABASE_URL / OPENAI_API_KEY 等

# 4. 跑迁移
make db-upgrade

# 5. 起 API
make api-dev
# -> http://localhost:8080/health

# 6. 起 Web (M1 S1.3)
make web-dev
# -> http://localhost:5173
```

## Makefile 速查

| 命令 | 作用 |
| --- | --- |
| `make api-dev` | 跑 idm-api (热重载) |
| `make api-test` | 跑 pytest |
| `make web-dev` | 跑 web console |
| `make db-upgrade` | Alembic 升级到 head |
| `make db-migrate msg="add col"` | 新建迁移 |
| `make db-downgrade` | Alembic 回退 1 步 |
| `make lint` | ruff + mypy |
| `make format` | ruff format |
| `make e2e` | 端到端 demo (M1 S1.2+) |
| `make k8s-apply` | ArgoCD sync (M3+) |

## 当前里程碑

- **M1 S1.1 (本 PR)**: 脚手架 + DB Schema v1 + idm-api `/health` + `/assets` CRUD
- **M1 S1.2**: CH MCP Server + discover_clickhouse_assets Skill + Asset 入库 + 简易 Web
- **M1 S1.3**: 全文搜索 / Tag / Owner 编辑 / Airflow Observer / Staging 部署
- **M2+**: Planner / 9 Agent / Skill Layer / Langfuse / ArgoCD (见 [roadmap.md](./docs/design/roadmap.md))

## 关键约定

详见 [AGENT_INSTRUCTIONS.md §13](./docs/AGENT_INSTRUCTIONS.md)：

- 资产 FQN: `<service>.<database>.<schema>.<table>` 全小写
- 全部 ID: `UUID DEFAULT gen_random_uuid()`
- 全表 `created_at` / `updated_at`
- MCP tool 命名: `<server>.<verb>`
- Skill 命名: `<verb>_<object>`
- Agent 命名: `<领域>Agent`

## 6 阶段真实数据管道 — 一键复现 (M1.5)

> 完整设计: [docs/design/data-pipeline-lineage.md](./docs/design/data-pipeline-lineage.md) §12

仓库自带完整 6 阶段 sample fixtures (GCS csv / Airflow DAG / Flink SQL / MEX io.yaml / Superset yaml / ClickHouse DDL),
**无需任何云凭证** 即可端到端跑通:

```bash
# 1. 起 API (PG + Redis 用 docker compose)
make api-dev

# 2. 在另一个终端, 一键跑 6 阶段管道 (fixture 模式)
cd idm
python trigger_pipeline_demo.py --api http://localhost:8000

# 3. 单阶段 / 重扫
python trigger_pipeline_demo.py --api http://localhost:8000 --stage 3
python trigger_pipeline_demo.py --api http://localhost:8000 --rescan

# 4. CI / 定时 (Bash / Batch)
bash scripts/rescan_pipeline.sh --full          # Linux/macOS
scripts\rescan_pipeline.bat --full              # Windows
```

**样例文件**:
- `fixtures/pipeline-demo/gcs/company-{raw,model-input,model-output}/orders/2026/06/*.csv`
- `fixtures/pipeline-demo/github/company/dwh/dags/etl_orders_daily.py`
- `fixtures/pipeline-demo/github/company/dwh/flink_jobs/{orders_preprocess,load_orders_risk_to_clickhouse}.sql`
- `fixtures/pipeline-demo/github/company/mex-models/orders/io.yaml`
- `fixtures/pipeline-demo/github/company/superset-export/dashboards.yml`
- `deploy/docker/seed-shop.sql` (ClickHouse DDL + 数据)
- `use_cases/shop-orders-mex-pipeline.yml` (完整 6 阶段 Use Case)

**全离线端到端验证** (无需起 API):
```bash
cd apps/api
MOCK_GCS_ROOT="$PWD/../fixtures/pipeline-demo/gcs" \
MOCK_GITHUB_ROOT="$PWD/../fixtures/pipeline-demo/github" \
uv run --no-progress python -m idm_api.verify_pipeline_fixtures
# 预期 9/9 stages passed
```

## 许可证

Internal — TBD
