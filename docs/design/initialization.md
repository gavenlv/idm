# IDM — 系统初始化与 Bootstrap (System Initialization Guide)

> 📌 **本文件是** IDM 平台从零到可用的"开局手册"。
> 覆盖: **PG / Alembic / ClickHouse / Redis / Langfuse / fixtures / sample data / use case 触发 / re-scan 入口**。
> 详细设计见: [architecture.md](./architecture.md) · [deployment.md](./deployment.md) · [data-pipeline-lineage.md](./data-pipeline-lineage.md) · [use-case-spec.md](./use-case-spec.md)

---

## 目录

- [0. 一句话目标](#0-一句话目标)
- [1. 系统依赖清单](#1-系统依赖清单)
- [2. 初始化总览 (3 层)](#2-初始化总览-3-层)
- [3. L1: 基础设施层 (PG / ClickHouse / Redis)](#3-l1-基础设施层-pg--clickhouse--redis)
- [4. L2: 数据层 (Alembic 迁移 + seed 数据)](#4-l2-数据层-alembic-迁移--seed-数据)
- [5. L3: 业务层 (Use Case + Sample Fixtures + Trigger)](#5-l3-业务层-use-case--sample-fixtures--trigger)
- [6. Re-scan 入口 (系统级 & 业务级)](#6-re-scan-入口-系统级--业务级)
- [7. 端到端初始化脚本 (一键)](#7-端到端初始化脚本-一键)
- [8. 验证与故障排查](#8-验证与故障排查)
- [9. 清理 (Clean-up)](#9-清理-clean-up)

---

## 0. 一句话目标

> 让一个新员工在 **30 分钟内** 把 IDM 完整 6 阶段管道跑通,
> 包含 PG / Alembic 迁移 / ClickHouse seed / sample fixtures / use case 触发 / re-scan。
> 0 真实云凭证, 0 真实数据。

---

## 1. 系统依赖清单

| 依赖 | 版本 | 安装命令 (macOS) | 安装命令 (Windows) | 用途 |
| --- | --- | --- | --- | --- |
| **Python** | 3.12+ | `brew install python@3.12` | `winget install Python.Python.3.12` | 跑 API + skill |
| **uv** | 0.4+ | `brew install uv` | `winget install astral-sh.uv` | Python 依赖管理 |
| **Docker** | 24+ | Docker Desktop | Docker Desktop | PG / ClickHouse / Redis / Langfuse |
| **psql client** | 16 | `brew install libpq` | `scoop install postgresql` | 调试 PG (可选) |
| **clickhouse-client** | 24+ | `brew install clickhouse` | `scoop install clickhouse` | 调试 CH (可选) |
| **redis-cli** | 7+ | `brew install redis` | `scoop install redis` | 调试 Redis (可选) |
| **git** | 2.40+ | `brew install git` | `winget install Git.Git` | clone + fixtures |
| **make / GNU coreutils** | latest | `brew install make` | Git Bash 自带 | 跑 Makefile |

> 📌 仓库已锁版本: `uv.lock` (API + KG), 锁 `pyproject.toml` (web), 不需要手动选版本。

---

## 2. 初始化总览 (3 层)

```mermaid
flowchart TB
    subgraph L1[Layer 1: 基础设施]
        A1[PG 16 + AGE + pgvector + pgcrypto + pg_trgm] --> A2[ClickHouse 24 + shop database]
        A2 --> A3[Redis 7 + Langfuse]
    end
    subgraph L2[Layer 2: 数据]
        B1[Alembic upgrade head] --> B2[seed-shop.sql 5 张表 + 100 行样例]
        B2 --> B3[idm-kg 14 张元数据表]
    end
    subgraph L3[Layer 3: 业务]
        C1[use_cases/*.yml] --> C2[fixtures/pipeline-demo/ GCS+GitHub 本地镜像]
        C2 --> C3[POST /use-cases/.../trigger 或 trigger_pipeline_demo.py]
        C3 --> C4[GET /api/v1/assets 验证 资产/血缘]
    end
    L1 --> L2 --> L3
```

**3 层关系**:
- **L1** 提供 6 阶段管道要用的存储 (PG 元数据 + ClickHouse 业务 + Redis 缓存)
- **L2** 把 IDM 自己的 schema 装到 L1 里
- **L3** 通过 Use Case + 触发端点, 把"业务上想跑"的事变成 KG 里的资产/血缘

---

## 3. L1: 基础设施层 (PG / ClickHouse / Redis)

### 3.1 启动本地 docker compose

```bash
cd idm
docker compose -f deploy/docker/compose.dev.yml up -d
# 等待 healthy:
docker compose -f deploy/docker/compose.dev.yml ps
# 期望:
#   idm-postgres   Up (healthy)
#   idm-redis      Up
#   idm-clickhouse Up
#   idm-langfuse   Up (healthy)
```

### 3.2 PG 自动初始化

`compose.dev.yml` 已经把 [init-postgres.sh](file:///d:/workspace/github-ai/idm/deploy/docker/init-postgres.sh) 挂到 `/docker-entrypoint-initdb.d/`, 首次启动会:

1. `apt-get install postgresql-16-age` (装 AGE 图查询)
2. `CREATE EXTENSION IF NOT EXISTS` 创建:
   - `uuid-ossp` (UUID 生成)
   - `pgcrypto` (加密 + `gen_random_uuid()`)
   - `pg_trgm` (三字符相似度索引)
   - `vector` (pgvector — 向量检索)
   - `age` (Apache AGE — 图查询)

**手动重跑 (容器已存在)**:

```bash
docker exec -i idm-postgres psql -U idm -d idm <<'EOSQL'
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS age;
SELECT extname, extversion FROM pg_extension ORDER BY extname;
EOSQL
```

### 3.3 ClickHouse 初始化 (seed-shop.sql)

容器自带 `shop` database (因为 `CLICKHOUSE_DB=shop` env), 但需要 seed 5 张样例表:

```bash
# 方式 A: docker cp (推荐)
docker cp idm/deploy/docker/seed-shop.sql idm-clickhouse:/tmp/
docker exec -i idm-clickhouse clickhouse-client --user=idm_ro --password=idm_ro \
    --multiquery < /tmp/seed-shop.sql

# 方式 B: 直接管道
docker exec -i idm-clickhouse clickhouse-client --user=idm_ro --password=idm_ro \
    --multiquery < idm/deploy/docker/seed-shop.sql
```

**预期结果**:
- 5 张表: `fct_orders_daily`, `fct_orders_risk_daily`, `dim_users`, `dim_country`, `stg_payments`
- 每张表 ~100 行测试数据
- 可用 `docker exec idm-clickhouse clickhouse-client -q "SELECT count() FROM shop.fct_orders_daily"` 验证

### 3.4 验证基础设施

```bash
# PG
docker exec idm-postgres pg_isready -U idm -d idm
docker exec idm-postgres psql -U idm -d idm -c "SELECT current_database(), version();"

# ClickHouse
docker exec idm-clickhouse clickhouse-client -q "SELECT version(), currentDatabase()"
docker exec idm-clickhouse clickhouse-client -q "SHOW TABLES FROM shop"

# Redis
docker exec idm-redis redis-cli PING
# 期望: PONG

# Langfuse
curl -sf http://localhost:13001/api/public/health
```

---

## 4. L2: 数据层 (Alembic 迁移 + seed 数据)

### 4.1 Alembic 迁移 (14 张 KG 表)

```bash
cd idm
# 查看当前版本
make db-current           # 或: cd apps/api && uv run alembic -c ../../migrations/alembic.ini current

# 升级到 head
make db-upgrade           # 或: cd apps/api && uv run alembic -c ../../migrations/alembic.ini upgrade head

# 看 SQL 预览 (不执行)
make db-upgrade-dry        # 或: cd migrations && uv run alembic upgrade head --sql

# 现状: 4 个 migration
#  0001_initial_schema.py         — services / databases / schemas / tables / columns
#  0002_data_quality_health.py    — quality_rules / quality_results
#  0003_data_pipeline_lineage.py  — pipelines / table_lineage
#  0004_pipeline_stage.py         — pipeline_stage 字段 + 索引
```

**预期结果**:

```bash
docker exec idm-postgres psql -U idm -d idm -c "\dt" | grep -E "^( services| databases| schemas| table_assets| column_assets| table_lineage| pipelines)"
# 14+ 张表, 包括 idm_kg 自己用的 ai_suggestion / audit_log / glossary / owner / tag / quality
```

### 4.2 创建 alembic 迁移 (改 schema 时)

```bash
make db-migrate msg="add col foo"     # 自动生成新文件
# 之后手动编辑 0005_*.py 修 upgrade() / downgrade()
make db-current                       # 确认新版本生效
```

### 4.3 回滚 / 重置

```bash
# 回退 1 步
make db-downgrade

# 完全重置 (清表 + 重跑)
docker exec idm-postgres psql -U idm -d idm -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
make db-upgrade
docker exec -i idm-clickhouse clickhouse-client --multiquery < idm/deploy/docker/seed-shop.sql
```

---

## 5. L3: 业务层 (Use Case + Sample Fixtures + Trigger)

### 5.1 安装 Python 依赖

```bash
cd idm
make install                  # uv sync --all-extras --dev
```

### 5.2 配置 .env

```bash
cp apps/api/.env.example apps/api/.env   # 如果有, 否则手动创建
# 必填项:
cat > apps/api/.env <<EOF
APP_ENV=local
DATABASE_URL=postgresql+asyncpg://idm:idm@localhost:5432/idm
DATABASE_URL_SYNC=postgresql+psycopg://idm:idm@localhost:5432/idm
CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=18123
CLICKHOUSE_USER=idm_ro
CLICKHOUSE_PASSWORD=idm_ro
CLICKHOUSE_DATABASE=shop
# === 本地 fixture 模式 (e2e 演示 / CI) ===
MOCK_GCS_ROOT=D:/workspace/github-ai/idm/fixtures/pipeline-demo/gcs
MOCK_GITHUB_ROOT=D:/workspace/github-ai/idm/fixtures/pipeline-demo/github
EOF
```

### 5.3 启动 API

```bash
make api-dev                  # uvicorn idm_api.main:app --reload --port 8080
# 另一个终端:
curl -sf http://localhost:8080/health/ready | head -c 200
```

### 5.4 检查 fixtures 是否到位

```bash
ls -la idm/fixtures/pipeline-demo/gcs/
# company-raw/orders/2026/06/orders-20260608.csv
# company-raw/orders/2026/06/orders-20260609.csv
# company-model-input/orders/2026/06/orders_enriched-20260608.csv
# company-model-output/orders/2026/06/orders_risk-20260608.csv

ls -la idm/fixtures/pipeline-demo/github/company/
# dwh/dags/etl_orders_daily.py
# dwh/flink_jobs/orders_preprocess.sql
# dwh/flink_jobs/load_orders_risk_to_clickhouse.sql
# mex-models/orders/io.yaml
# superset-export/dashboards.yml
```

### 5.5 检查 use case

```bash
cat idm/use_cases/shop-orders-mex-pipeline.yml | head -30
# 期望含 id/version/description/owners/sources/analysis
```

### 5.6 触发完整 6 阶段管道

**方式 A — 脚本 (推荐)**:

```bash
cd idm
python trigger_pipeline_demo.py --api http://localhost:8080
# 期望末尾: "ok": true
```

**方式 B — curl (业务入口)**:

```bash
curl -sf -X POST http://localhost:8080/api/v1/use-cases/shop-orders-mex-pipeline/trigger \
  -H 'Content-Type: application/json' \
  -d '{"use_case_id":"shop-orders-mex-pipeline","apply":true}' | jq .ok
```

**方式 C — 单阶段 (例如重扫 MEX)**:

```bash
curl -sf -X POST http://localhost:8080/api/v1/use-cases/shop-orders-mex-pipeline/stages/3/trigger \
  -H 'Content-Type: application/json' -d '{}' | jq .ok
```

**方式 D — 全离线端到端 (不起 API)**:

```bash
cd apps/api
MOCK_GCS_ROOT="$PWD/../fixtures/pipeline-demo/gcs" \
MOCK_GITHUB_ROOT="$PWD/../fixtures/pipeline-demo/github" \
uv run --no-progress python -m idm_api.verify_pipeline_fixtures
# 期望 9/9 stages passed
```

### 5.7 验证结果

```bash
# 资产数
curl -sf http://localhost:8080/api/v1/assets | jq '.items | length'

# 血缘边数
curl -sf "http://localhost:8080/api/v1/lineage?fqn=gcs://company-raw/orders/2026/06/orders-20260608.csv" | jq

# Skill 注册
curl -sf http://localhost:8080/api/v1/skills | jq '.items[] | .name' | head

# MCP 健康
curl -sf http://localhost:8080/api/v1/skills/mcp/health | jq
```

---

## 6. Re-scan 入口 (系统级 & 业务级)

> 资产/血缘是"活的" — 上游数据可能变化, 需要定期或按需重扫。
> IDM 提供 **2 套入口**: 业务级 (按 use case) + 系统级 (按 source_type)。

### 6.1 业务级: 按 use case re-scan

```bash
# 完整 re-scan
curl -sf -X POST http://localhost:8080/api/v1/use-cases/shop-orders-mex-pipeline/rescan \
  -H 'Content-Type: application/json' -d '{"apply":true}' | jq .ok

# 单阶段 re-scan (只扫阶段 5: Flink load + ClickHouse)
curl -sf -X POST http://localhost:8080/api/v1/use-cases/shop-orders-mex-pipeline/stages/5/trigger \
  -H 'Content-Type: application/json' -d '{}' | jq .ok

# 多阶段 (阶段 1 + 5)
curl -sf -X POST http://localhost:8080/api/v1/use-cases/shop-orders-mex-pipeline/trigger \
  -H 'Content-Type: application/json' -d '{"stages":[1,5]}' | jq .ok
```

**幂等保证**: 资产/血缘 全部走 upsert (按 fqn / (upstream, downstream) 唯一), 多次调用无副作用。

### 6.2 系统级: 按 source_type re-scan

> **不依赖 use case** — 用于: 平台 onboarding / 失败恢复 / ChatOps 触发。

```bash
# 扫 GCS (按 bucket, 跑 stage=1,2,4)
curl -sf -X POST http://localhost:8080/api/v1/scan/asset \
  -H 'Content-Type: application/json' \
  -d '{"source_type":"gcs","bucket":"company-raw"}' | jq

# 扫 ClickHouse (按 database)
curl -sf -X POST http://localhost:8080/api/v1/scan/asset \
  -H 'Content-Type: application/json' \
  -d '{"source_type":"clickhouse","database":"shop"}' | jq

# 扫 Superset
curl -sf -X POST http://localhost:8080/api/v1/scan/asset \
  -H 'Content-Type: application/json' \
  -d '{"source_type":"superset_export","service_name":"superset-demo"}' | jq

# 全扫 (GCS + CH + Superset)
curl -sf -X POST http://localhost:8080/api/v1/scan/asset \
  -H 'Content-Type: application/json' \
  -d '{"source_type":"all"}' | jq
```

**响应**:
```json
{
  "ok": true,
  "source_type": "gcs",
  "items_count": 8,
  "by_subtype": {"gcs_object": 8},
  "output": {"blocks": [...]},
  "duration_ms": 1234
}
```

### 6.3 入口选择决策树

```
我要 re-scan ...
├── 业务上希望"按 use case 重跑" (业务人员/UI)
│   └── POST /api/v1/use-cases/{id}/rescan
├── 我改了 use case YAML 想再跑一次
│   └── POST /api/v1/use-cases/{id}/trigger
├── 我只关心某阶段 (例如 MEX)
│   └── POST /api/v1/use-cases/{id}/stages/{n}/trigger
├── 刚接了一个新 GCS bucket, 没在 use case 里
│   └── POST /api/v1/scan/asset  {source_type: gcs, bucket: ...}
├── 周期任务 (CronJob) 想"全部资产扫一遍"
│   └── POST /api/v1/scan/asset  {source_type: all}
└── ChatOps / Slack bot 触发
    └── 同上 (用 curl / webhook)
```

### 6.4 CI / Cron 集成

```bash
# Linux: 每 6 小时自动 re-scan 整条管道
echo "0 */6 * * *  /usr/local/bin/idm-rescan --all" >> /etc/crontab
# (假设 /usr/local/bin/idm-rescan 是 wrapper: trigger_pipeline_demo.py --rescan)

# Windows Task Scheduler
schtasks /Create /SC HOURLY /MO 6 /TN "idm-rescan" /TR "D:\workspace\github-ai\idm\scripts\rescan_pipeline.bat --full"
```

### 6.5 幂等 / 失败安全

- ✅ 资产/血缘 全部 upsert — 多次调用结果一致
- ✅ 失败 stage 不会让后续 stage 中断 (`analyze_data_pipeline` 内部 try/except)
- ✅ 单步超时 30s (AGENT_INSTRUCTIONS §0.5 铁律)
- ✅ 大管道 client 应 streaming, 不阻塞

---

## 7. 端到端初始化脚本 (一键)

将以下命令保存为 `idm/scripts/bootstrap.sh` (Linux) / `bootstrap.bat` (Windows):

```bash
#!/usr/bin/env bash
# scripts/bootstrap.sh — 从零到完整可用的 5 步
set -e

echo "==> [1/5] Start infra (PG + ClickHouse + Redis + Langfuse)"
cd "$(dirname "$0")/.."
docker compose -f deploy/docker/compose.dev.yml up -d
sleep 8

echo "==> [2/5] Seed ClickHouse shop database"
docker cp deploy/docker/seed-shop.sql idm-clickhouse:/tmp/
docker exec -i idm-clickhouse clickhouse-client --user=idm_ro --password=idm_ro --multiquery < /tmp/seed-shop.sql

echo "==> [3/5] Alembic upgrade head"
cd apps/api
uv run --no-progress alembic -c ../../migrations/alembic.ini upgrade head
cd ../..

echo "==> [4/5] Verify fixtures"
test -f fixtures/pipeline-demo/gcs/company-raw/orders/2026/06/orders-20260608.csv
test -f use_cases/shop-orders-mex-pipeline.yml
echo "  fixtures OK"

echo "==> [5/5] Run end-to-end 6-stage pipeline (offline, no API)"
cd apps/api
MOCK_GCS_ROOT="$PWD/../fixtures/pipeline-demo/gcs" \
MOCK_GITHUB_ROOT="$PWD/../fixtures/pipeline-demo/github" \
uv run --no-progress python -m idm_api.verify_pipeline_fixtures
# 期望: 9/9 stages passed

echo ""
echo "==> ALL DONE."
echo "    Next: make api-dev    (or: cd apps/api && uv run uvicorn idm_api.main:app --port 8080)"
echo "    Then: python trigger_pipeline_demo.py --api http://localhost:8080"
```

> ⏱ 全流程约 **2 分钟** (主要耗时: docker pull + alembic)。

---

## 8. 验证与故障排查

### 8.1 验证清单 (全 ✓ = 平台就绪)

- [ ] `docker compose ps` — 4 个容器全 healthy
- [ ] `psql -U idm -c "SELECT extname FROM pg_extension"` — 5 个扩展齐
- [ ] `clickhouse-client -q "SHOW TABLES FROM shop"` — 5 张表
- [ ] `alembic current` — 0004 (head)
- [ ] `curl /health/ready` → 200
- [ ] `python -m idm_api.verify_pipeline_fixtures` → 9/9
- [ ] `trigger_pipeline_demo.py --rescan` → ok=true
- [ ] `GET /api/v1/assets` → 资产数 ≥ 20
- [ ] `GET /api/v1/skills` → 13+ skill

### 8.2 常见故障

| 现象 | 检查 |
| --- | --- |
| `psql: connection to server failed` | `docker compose ps idm-postgres` 是否 healthy; `lsof -i :5432` 是否被其他 PG 占用 |
| `alembic: No module named 'idm_kg'` | `cd apps/api && uv sync` 装依赖 |
| `verify_pipeline_fixtures: 0 stages` | `echo $MOCK_GCS_ROOT` 是否指对 `fixtures/pipeline-demo/gcs` |
| `trigger /rescan: use case not found` | `ls use_cases/*.yml` 有 `shop-orders-mex-pipeline.yml` |
| `ClickHouse: Authentication failed` | `.env` 里 `CLICKHOUSE_USER=idm_ro` `CLICKHOUSE_PASSWORD=idm_ro` |
| Superset `superset_unreachable` | fixture 模式用 `dry_run=true`; 真实环境设 `SUPERSET_URL` |
| 血缘边没写出 (edges_inferred=0) | source/sink FQN 拼写 + KG 里是否已有资产; Flink SQL 中 `clickhouse-prod` → `clickhouse_prod` |
| `pg_isready` 一直 timeout | PG 容器 first start 慢 (~30s); 多等 + 看 logs |

### 8.3 性能基线 (M1.5 实测)

| 操作 | 端到端耗时 | 备注 |
| --- | --- | --- |
| 6 阶段全跑 (fixture 模式) | 5-8s | 主要是 LLM mock 调用 |
| GCS 单 bucket 扫 | 0.3s | local file system |
| ClickHouse 5 表扫 | 1.2s | 取决于 `describe_table` 延迟 |
| MEX io.yaml 解析 | 0.1s | yaml 解析 |
| 全量 re-scan (idempotent) | 6-10s | 跟首次几乎一致 |
| Alembic upgrade head | 1-3s | 4 个 migration |

---

## 9. 清理 (Clean-up)

### 9.1 不删数据, 只清缓存

```bash
make clean                  # 清 __pycache__ / .pytest_cache / .ruff_cache / .mypy_cache
```

### 9.2 重置 PG (元数据全清)

```bash
docker exec idm-postgres psql -U idm -d idm -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
make db-upgrade             # 重建 schema
```

### 9.3 重置 ClickHouse (业务数据全清)

```bash
docker exec idm-clickhouse clickhouse-client --multiquery \
  -q "DROP DATABASE IF EXISTS shop; CREATE DATABASE shop;"
docker exec -i idm-clickhouse clickhouse-client --user=idm_ro --password=idm_ro \
  --multiquery < idm/deploy/docker/seed-shop.sql
```

### 9.4 删容器 + 卷 (完全重置)

```bash
docker compose -f deploy/docker/compose.dev.yml down -v
# -v: 同时删 idm_pgdata / idm_redis / idm_chdata 卷
# 注意: 之后所有数据全丢, 需重跑 §3-5
```

### 9.5 只重跑初始化 (PG/CH 数据保留)

```bash
# 1) 重跑 alembic (幂等, 表已存在会 no-op)
make db-upgrade

# 2) 重跑 ClickHouse seed (用 INSERT OR REPLACE 语义: DROP + CREATE)
# 已在 §9.3 给出

# 3) 重触发 6 阶段管道
python trigger_pipeline_demo.py --api http://localhost:8080 --rescan
```

---

## 附录 A: 环境变量速查

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `APP_ENV` | `local` | local / dev / staging / prod, 影响日志 + docs URL |
| `DATABASE_URL` | `postgresql+asyncpg://idm:idm@localhost:5432/idm` | async DSN (运行时) |
| `DATABASE_URL_SYNC` | `postgresql+psycopg://idm:idm@localhost:5432/idm` | sync DSN (alembic 用) |
| `CLICKHOUSE_HOST` | `localhost` | CH MCP target |
| `CLICKHOUSE_PORT` | `8123` | HTTP 端口 (生产用 9000 native) |
| `CLICKHOUSE_USER` | `idm_ro` | 只读账号 (IDM 仅 SELECT) |
| `CLICKHOUSE_PASSWORD` | `idm_ro` | |
| `MOCK_GCS_ROOT` | (空) | 离线模式: 本地 GCS 根目录 |
| `MOCK_GITHUB_ROOT` | (空) | 离线模式: 本地 GitHub 根目录 |
| `MCP_GITHUB_TOKEN` | (空) | 真实 GitHub PAT (含 `repo` + `read:org`) |
| `SUPERSET_URL` | `http://localhost:8088` | 真实 Superset base URL |
| `SUPERSET_USERNAME` | `admin` | |
| `SUPERSET_PASSWORD` | `admin` | |
| `IDM_USE_CASES_DIR` | (空) | 覆盖 use case 目录 (默认 `<repo>/use_cases`) |
| `IDM_USE_CASE_WATCHER` | `0` | 启用 use case file watcher (M5) |

---

## 附录 B: Make / Idempotent 命令速查

| 命令 | 用途 | 幂等 |
| --- | --- | --- |
| `make install` | 同步 Python 依赖 | ✅ |
| `make api-dev` | 起 API (热重载) | ✅ |
| `make db-up` | 起 PG 容器 | ✅ |
| `make db-upgrade` | Alembic 升级 | ✅ |
| `make db-downgrade` | Alembic 回退 1 步 | ✅ |
| `make db-migrate msg=...` | 新建 migration | ❌ (一次性) |
| `make lint` | ruff + mypy | ✅ |
| `make test` | 全量测试 | ✅ |
| `make clean` | 清缓存 | ✅ |
| `make clean-all` | 清缓存 + .venv | ❌ (会删 venv) |

---

> 📌 **配套阅读**: [architecture.md](./architecture.md) §6 技术栈 · [deployment.md](./deployment.md) §5 存储 · [data-pipeline-lineage.md](./data-pipeline-lineage.md) §12 样例 · [AGENT_INSTRUCTIONS.md §0.5](../AGENT_INSTRUCTIONS.md) 自动化铁律
