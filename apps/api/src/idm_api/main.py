"""idm-api: FastAPI 入口 (M1 脚手架).

启动: uvicorn idm_api.main:app --reload
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from idm_api import __version__
from idm_api.config import get_settings
from idm_api.db import dispose_engine, get_engine
from idm_api.routers import assets, health, services, suggestions

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """启动 / 关闭钩子。"""
    # 启动: 预热 engine, 跑迁移前检查
    engine = get_engine()
    async with engine.connect() as conn:
        from sqlalchemy import text

        await conn.execute(text("SELECT 1"))
    yield
    # 关闭
    await dispose_engine()


app = FastAPI(
    title="IDM API",
    description="Intelligent Data Mesh — AI-driven data management platform API.",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    openapi_url="/openapi.json" if not settings.is_production else None,
)

# === CORS ===
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Routers ===
app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(services.router, prefix="/api/v1/services", tags=["services"])
app.include_router(assets.router, prefix="/api/v1/assets", tags=["assets"])
app.include_router(suggestions.router, prefix="/api/v1/suggestions", tags=["suggestions"])


__all__ = ["app", "__version__"]
