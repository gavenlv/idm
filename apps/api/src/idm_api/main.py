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
from idm_api.routers import (
    assets, chatbi, column_lineage, descriptions, feedback, glossary, health, idm_self_mcp, impact, openlineage, owners, quality, scan, search, services, skills, suggestions, tags, use_case_trigger, use_cases,
)
from idm_api.skills import mcp as mcp_sidecar  # 注册 builtin skills via import side-effect

# 显式 import builtin skills 让 @skill 装饰器触发
import idm_api.skills.builtin.discover_clickhouse_assets  # noqa: F401
import idm_api.skills.builtin.infer_table_description  # noqa: F401
import idm_api.skills.builtin.classify_pii_columns  # noqa: F401
import idm_api.skills.builtin.parse_dbt_manifest  # noqa: F401
import idm_api.skills.builtin.analyze_dbt_code  # noqa: F401
import idm_api.skills.builtin.parse_superset_dashboard  # noqa: F401
import idm_api.skills.builtin.infer_table_owners  # noqa: F401
import idm_api.skills.builtin.nl2sql  # noqa: F401
import idm_api.skills.builtin.detect_anomalies  # noqa: F401
import idm_api.skills.builtin.map_glossary  # noqa: F401
# === M3 Lineage ===
import idm_api.skills.builtin.parse_airflow_dag  # noqa: F401
import idm_api.skills.builtin.extract_sql_lineage  # noqa: F401
import idm_api.skills.builtin.lineage_reasoner  # noqa: F401
# === M4 Quality + Insight + Profiler ===
import idm_api.skills.builtin.run_quality_check  # noqa: F401
import idm_api.skills.builtin.compose_insight  # noqa: F401
import idm_api.skills.builtin.profiler  # noqa: F401
# === M2.x Semantic Enrichment ===
import idm_api.skills.builtin.infer_column_descriptions  # noqa: F401
import idm_api.skills.builtin.infer_column_lineage  # noqa: F401
import idm_api.skills.builtin.lineage_to_column  # noqa: F401
import idm_api.skills.builtin.infer_lineage_descriptions  # noqa: F401
# === M2.5 OpenLineage Alignment ===
import idm_api.skills.builtin.emit_openlineage_event  # noqa: F401

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """启动 / 关闭钩子。"""
    # 启动: 预热 engine, 跑迁移前检查
    engine = get_engine()
    async with engine.connect() as conn:
        from sqlalchemy import text

        await conn.execute(text("SELECT 1"))
    # 启动 MCP sidecar
    async with mcp_sidecar.mcp_lifespan():
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
app.include_router(skills.router, prefix="/api/v1/skills", tags=["skills"])
app.include_router(owners.router, prefix="/api/v1/owners", tags=["owners"])
app.include_router(tags.router, prefix="/api/v1/tags", tags=["tags"])
app.include_router(glossary.router, prefix="/api/v1/glossary", tags=["glossary"])
app.include_router(use_cases.router, prefix="/api/v1/use-cases", tags=["use-cases"])
app.include_router(use_case_trigger.router, prefix="/api/v1/use-cases", tags=["use-cases"])
app.include_router(scan.router, prefix="/api/v1/scan", tags=["scan"])
app.include_router(search.router, prefix="/api/v1/search", tags=["search"])
app.include_router(impact.router, prefix="/api/v1/impact", tags=["impact"])
app.include_router(quality.router, prefix="/api/v1/quality", tags=["quality"])
app.include_router(chatbi.router, prefix="/api/v1/chatbi", tags=["chatbi"])
app.include_router(idm_self_mcp.router, prefix="/api/v1/mcp/idm-self", tags=["mcp-idm-self"])
app.include_router(feedback.router)  # 内部已含 /api/v1/feedback prefix

# === M2.x Semantic Enrichment ===
app.include_router(column_lineage.router, prefix="/api/v1", tags=["lineage-column"])
app.include_router(descriptions.description_router, prefix="/api/v1", tags=["descriptions"])
app.include_router(descriptions.asset_description_router, prefix="/api/v1", tags=["descriptions"])
app.include_router(descriptions.lineage_description_router, prefix="/api/v1", tags=["descriptions"])
# === M2.5 OpenLineage Alignment ===
app.include_router(openlineage.router, prefix="/api/v1", tags=["lineage-openlineage"])


__all__ = ["app", "__version__"]
