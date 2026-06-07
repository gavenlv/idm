# IDM API Dockerfile
# Multi-stage build: builder (uv) + runtime (slim)
# Build: docker build -t idm-api:dev -f apps/api/Dockerfile .
# Run:   docker run -p 8080:8080 --env-file .env idm-api:dev

# === 1. Builder ===
FROM python:3.11-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# uv
RUN pip install --no-cache-dir uv==0.5.10

WORKDIR /app

# 先 copy 依赖文件, 利用缓存
COPY pyproject.toml uv.lock* ./
COPY apps/api/pyproject.toml apps/api/
COPY packages/kg/pyproject.toml packages/kg/

# 装依赖到 /app/.venv
RUN uv sync --frozen --no-dev --no-install-project

# 再 copy 源码
COPY apps/api/src apps/api/src
COPY packages/kg/src packages/kg/src
COPY migrations migrations

# 安装项目本身
RUN uv sync --frozen --no-dev

# === 2. Runtime ===
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/apps/api/src:/app/packages/kg/src

# 非 root
RUN groupadd -r idm && useradd -r -g idm idm

# 从 builder 拷
COPY --from=builder --chown=idm:idm /app /app

USER idm
WORKDIR /app/apps/api

EXPOSE 8080

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8080/health', timeout=3).raise_for_status()" || exit 1

CMD ["uvicorn", "idm_api.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2", "--proxy-headers"]
