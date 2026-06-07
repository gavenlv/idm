# IDM — Top-level Makefile
# M1 起步: 脚手架 + API + DB
SHELL := pwsh.exe
.SHELLFLAGS := -NoProfile -Command
.DEFAULT_GOAL := help

# === 变量 ===
PY              ?= py
UV              ?= uv
PYTHON          ?= python
PORT_API        ?= 8080
PORT_WEB        ?= 5173
ENV_FILE        ?= .env
COMPOSE_FILE    ?= deploy/docker/compose.dev.yml
MIGRATIONS_DIR  ?= migrations

# === 帮助 ===
.PHONY: help
help: ## 列出所有目标
	@$(MAKE) -p 2>&1 | Select-String -Pattern '^[a-zA-Z_-]+:.*?##' | ForEach-Object { $$_ -replace ':.*?##', ' | ' }

# === 环境 ===
.PHONY: install
install: ## 同步依赖 (uv)
	$(UV) sync --all-extras --dev

.PHONY: install-ci
install-ci: ## CI 模式同步 (无 lock 变更)
	$(UV) sync --frozen --all-extras --dev

# === API (apps/api) ===
.PHONY: api-dev
api-dev: ## 跑 idm-api (热重载, 端口 $(PORT_API))
	cd apps/api && $(UV) run uvicorn idm_api.main:app --reload --host 0.0.0.0 --port $(PORT_API)

.PHONY: api-test
api-test: ## 跑 API 测试
	cd apps/api && $(UV) run pytest $(ARGS)

.PHONY: api-run
api-run: ## 跑 idm-api (生产模式)
	cd apps/api && $(UV) run uvicorn idm_api.main:app --host 0.0.0.0 --port $(PORT_API) --workers 2

# === Web (apps/web, M1 S1.3+) ===
.PHONY: web-install
web-install: ## 安装 web 依赖
	cd apps/web && npm install

.PHONY: web-dev
web-dev: ## 跑 web console (端口 $(PORT_WEB))
	cd apps/web && npm run dev -- --host 0.0.0.0 --port $(PORT_WEB)

.PHONY: web-build
web-build: ## 构建 web 生产包
	cd apps/web && npm run build

# === DB / Migrations ===
.PHONY: db-up
db-up: ## 启动本地 Postgres (docker compose)
	docker compose -f $(COMPOSE_FILE) up -d postgres
	@echo "等待 postgres 就绪..."
	@$i = 0; while ($$i -lt 30) { try { docker exec idm-postgres pg_isready -U idm | Out-Null; break } catch { Start-Sleep -Seconds 1; $$i++ } }

.PHONY: db-down
db-down: ## 停止本地 Postgres
	docker compose -f $(COMPOSE_FILE) down

.PHONY: db-upgrade
db-upgrade: ## 跑 Alembic 升级到 head
	cd apps/api && $(UV) run alembic -c ../../$(MIGRATIONS_DIR)/alembic.ini upgrade head

.PHONY: db-downgrade
db-downgrade: ## Alembic 回退 1 步
	cd apps/api && $(UV) run alembic -c ../../$(MIGRATIONS_DIR)/alembic.ini downgrade -1

.PHONY: db-migrate
db-migrate: ## 新建迁移 (使用: make db-migrate msg="add col")
	@if ([string]::IsNullOrEmpty("$(msg)")) { Write-Error "msg 参数必填: make db-migrate msg=..."; exit 1 }
	cd apps/api && $(UV) run alembic -c ../../$(MIGRATIONS_DIR)/alembic.ini revision --autogenerate -m "$(msg)"

.PHONY: db-current
db-current: ## 查看当前迁移版本
	cd apps/api && $(UV) run alembic -c ../../$(MIGRATIONS_DIR)/alembic.ini current

.PHONY: db-history
db-history: ## 查看迁移历史
	cd apps/api && $(UV) run alembic -c ../../$(MIGRATIONS_DIR)/alembic.ini history

# === Lint / Format / Type ===
.PHONY: lint
lint: ## ruff + mypy
	$(UV) run ruff check .
	$(UV) run mypy apps packages

.PHONY: format
format: ## ruff format + check fix
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

.PHONY: pre-commit
pre-commit: ## 跑 pre-commit hooks
	$(UV) run pre-commit run --all-files

# === Tests ===
.PHONY: test
test: ## 跑全量测试
	$(UV) run pytest

.PHONY: test-cov
test-cov: ## 测试 + 覆盖率报告
	$(UV) run pytest --cov-report=html
	@echo "覆盖率报告: htmlcov/index.html"

# === 清理 ===
.PHONY: clean
clean: ## 清缓存
	Get-ChildItem -Path . -Include __pycache__,.pytest_cache,.ruff_cache,.mypy_cache,*.egg-info -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
	Get-ChildItem -Path . -Include .coverage,htmlcov -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force

.PHONY: clean-all
clean-all: clean ## 清缓存 + .venv
	Remove-Item -Recurse -Force .venv,node_modules -ErrorAction SilentlyContinue

# === K8s / Deploy (M3+) ===
.PHONY: helm-lint
helm-lint: ## Helm lint
	helm lint deploy/helm/idm-api

.PHONY: k8s-apply
k8s-apply: ## ArgoCD sync (M3+)
	argocd app sync idm
