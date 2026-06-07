# IDM — M1 S1.1 构建状态 (Foundation)

## ✅ 已完成 (M1 S1.1)

### 1. 根配置
- [x] `pyproject.toml` (uv workspace marker)
- [x] `Makefile` (dev / test / db / lint / format / k8s)
- [x] `.gitignore` (Python / Node / IDE / Secrets / K8s)
- [x] `.env.example` (12-factor 全量环境变量)
- [x] `.pre-commit-config.yaml` (ruff / mypy / 禁 Antd / 禁 LLM 直调 MCP)
- [x] `README.md` (结构 + 快速开始 + 关键约定)
- [x] `deploy/docker/compose.dev.yml` (PG+AGE+pgvector / Redis / CH / Langfuse)
- [x] `deploy/docker/init-postgres.sh` (扩展开启)

### 2. apps/api (FastAPI)
- [x] `config.py` — Pydantic Settings
- [x] `db.py` — 异步 engine + session 工厂 + FastAPI 依赖
- [x] `main.py` — FastAPI app + CORS + lifespan
- [x] `schemas.py` — Pydantic v2 DTO
- [x] `routers/health.py` — `/health`, `/health/ready`, `/health/info`
- [x] `routers/services.py` — `/api/v1/services` CRUD
- [x] `routers/assets.py` — `/api/v1/assets` CRUD
- [x] `routers/suggestions.py` — `/api/v1/suggestions/{approve,reject}`
- [x] `tests/conftest.py` — SQLite in-memory 测试夹具
- [x] `tests/test_health.py`
- [x] `tests/test_services.py`
- [x] `tests/test_suggestions.py`
- [x] `Dockerfile` (multi-stage builder + runtime, non-root, healthcheck)

### 3. packages/kg (知识图谱模型)
- [x] `models/base.py` — DeclarativeBase + UUID/Timestamp Mixin + 命名约定
- [x] `models/service.py` (数据源)
- [x] `models/database.py`
- [x] `models/schema.py`
- [x] `models/table_asset.py` (核心资产)
- [x] `models/column_asset.py` (含 PII 字段)
- [x] `models/table_lineage.py` (血缘边)
- [x] `models/tag.py` (Tag + AssetTag)
- [x] `models/owner.py` (Owner / Steward / Consumer)
- [x] `models/glossary.py` (术语 + 资产绑定)
- [x] `models/quality.py` (规则 + 时序结果)
- [x] `models/ai_suggestion.py` (LLM 建议审核表)
- [x] `models/audit_log.py` (审计)
- [x] `tests/test_models.py`

### 4. Migrations
- [x] `migrations/alembic.ini`
- [x] `migrations/env.py` (用 settings 注入 DSN)
- [x] `migrations/script.py.mako`
- [x] `migrations/versions/0001_initial_schema.py` — **14 张表全量建表**

### 5. apps/web (React 18 + Vite + ag-grid)
- [x] `package.json` (ag-grid 32, react-query 5, reactflow 11, echarts 5)
- [x] `vite.config.ts` (代理 /api)
- [x] `tsconfig.json` (strict)
- [x] `index.html`
- [x] `src/main.tsx` (QueryClient + ThemeProvider + Router)
- [x] `src/App.tsx` (Header + 3 路由)
- [x] `src/lib/api.ts` (axios + 类型化 API)
- [x] `src/pages/AssetsPage.tsx` (ag-grid + 抽屉详情)
- [x] `src/pages/SuggestionsPage.tsx` (ag-grid + 批准/拒绝)
- [x] `src/pages/HealthPage.tsx` (服务信息 + 探活)
- [x] `src/ui/Button.tsx` / `Card.tsx` / `Tag.tsx` / `Drawer.tsx` / `index.ts` (IDM UI Kit 起步)
- [x] `src/ui/ThemeProvider.tsx`
- [x] `src/styles/global.css` (CSS Vars 占位)
- [x] `Dockerfile` (builder + nginx runtime)
- [x] `nginx.conf` (SPA + /api 反代)

### 6. Use Cases (业务入口)
- [x] `use_cases/schema.json` (JSON Schema Draft 2020-12 校验)
- [x] `use_cases/_templates/basic.yml`
- [x] `use_cases/shop-orders-daily.yml` — **首个真实示例** (CH + GH + Superset + AF)
- [x] `use_cases/README.md`

### 7. CI / CD
- [x] `.github/workflows/ci.yml` (lint + test multi-py + build)
- [x] `deploy/helm/idm-api/Chart.yaml` (GKE chart 骨架)
- [x] `deploy/argocd/idm-api.yaml` (GitOps App)

### 8. 设计文档更新
- [x] `docs/AGENT_INSTRUCTIONS.md` — **新增** (宪法级摘要)
- [x] `docs/design/architecture.md` — 顶部加引用
- [x] `docs/design/ai-driven-design.md` — 同步 GPT-5/DeepSeek/Qwen, 顶部加引用
- [x] `docs/design/data-model.md` — 顶部加引用
- [x] `docs/design/deployment.md` — 同步选型, 顶部加引用
- [x] `docs/design/mcp-first-architecture.md` — 同步前端/LLM
- [x] `docs/design/roadmap.md` — 同步选型
- [x] `docs/design/stack-decisions.md` — 同步选型
- [x] `docs/design/walkthrough.md` — 顶部加引用

---

## 🟡 M1 S1.1 验证步骤 (本地)

```powershell
# 1. 装 uv
py -m pip install uv

# 2. 同步依赖
cd d:\workspace\github-ai\idm
uv sync --all-extras --dev

# 3. 起 PG / Redis / CH
docker compose -f deploy/docker/compose.dev.yml up -d

# 4. 配置
cp .env.example .env

# 5. 跑迁移
make db-upgrade

# 6. 起 API
make api-dev
# -> http://localhost:8080/docs
# -> http://localhost:8080/health/ready

# 7. (另一终端) 起 Web
make web-install
make web-dev
# -> http://localhost:5173
```

## 🟡 M1 S1.1 自动化测试

```powershell
# 跑测试 (不需 docker, 用 SQLite)
uv run pytest -v

# 跑 lint
make lint
```

---

## ⏭️ 下一步: M1 S1.2 (1~2 周)

| 任务 | 优先级 | 工作量 |
| --- | --- | --- |
| CH MCP Server (Sidecar 模式) | P0 | 1d |
| Skill Runner 骨架 (Python) | P0 | 2d |
| `discover_clickhouse_assets` Skill | P0 | 2d |
| Asset 入库 (click → KG) | P0 | 1d |
| 简易 Web 资产详情页 (侧栏) | P1 | 2d |
| `infer_table_description` Skill (gpt-5) | P1 | 2d |
| Staging GKE 部署 | P1 | 2d |

## ⏭️ M1 S1.3

- 全文搜索 (pg_trgm + pgvector)
- Tag / Owner 编辑 UI
- Airflow Observer (MCP)
- Superset Export 解析

## ⏭️ M2 起步 (4 周)

- Planner Agent (LangGraph)
- 9 Specialist Agent
- Skill Eval Harness
- Langfuse 集成
- LLM Router (LiteLLM)
