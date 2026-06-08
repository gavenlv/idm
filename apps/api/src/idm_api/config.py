"""idm-api 配置 (Pydantic Settings, 12-factor)."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置, 自动从 .env 读取。

    分组:
    - app_*: 运行时
    - database_*: DB
    - clickhouse_*: 数据源
    - llm_*: 模型路由
    - langfuse_*: LLM 观测
    - mcp_*: MCP client
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # === Runtime ===
    app_env: Literal["local", "dev", "staging", "prod"] = "local"
    app_name: str = "idm-api"
    log_level: str = "INFO"
    log_json: bool = False

    api_host: str = "0.0.0.0"
    api_port: int = 8080
    api_cors_origins: str = "http://localhost:5173"
    api_workers: int = 1

    # === Database ===
    database_url: str = "postgresql+asyncpg://idm:idm@localhost:5432/idm"
    database_url_sync: str = "postgresql+psycopg://idm:idm@localhost:5432/idm"
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout: int = 30

    # === ClickHouse (MCP target) ===
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_database: str = "shop"
    clickhouse_user: str = "idm_ro"
    clickhouse_password: str = "idm_ro"
    clickhouse_secure: bool = False
    clickhouse_verify: bool = False

    # === LLM ===
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:32b"

    idm_llm_planner_model: str = "gpt-5"
    idm_llm_default_model: str = "gpt-5"
    idm_llm_cheap_model: str = "deepseek-v3"
    idm_llm_local_model: str = "qwen2.5:32b"
    idm_llm_pii_model: str = "qwen2.5:32b"

    # === Langfuse ===
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3001"
    langfuse_enabled: bool = False

    # === MCP ===
    mcp_clickhouse_transport: str = "stdio"
    mcp_github_transport: str = "stdio"
    mcp_github_token: str = ""
    mcp_gcs_transport: str = "stdio"
    mcp_slack_bot_token: str = ""

    # === Superset ===
    superset_url: str = "http://localhost:8088"
    superset_username: str = "admin"
    superset_password: str = "admin"
    superset_verify_ssl: bool = False

    # === GCS / Storage ===
    gcs_bucket_idm: str = "idm-artifacts-local"
    google_application_credentials: str = ""

    # === Redis ===
    redis_url: str = "redis://localhost:6379/0"

    # === 本地 fixture 模式 (离线 / 演示 / e2e 测试) ===
    # 当 google.cloud.storage 不可用时, GCS MCP 会回退到 mock.
    # 在 mock 模式下, 如果下面两个路径存在, 优先用本地文件模拟 GCS / GitHub 仓库.
    mock_gcs_root: str = ""         # 形如 D:\\workspace\\github-ai\\idm\\fixtures\\gcs
    mock_github_root: str = ""      # 形如 D:\\workspace\\github-ai\\idm\\fixtures\\github

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.app_env == "prod"


@lru_cache
def get_settings() -> Settings:
    """单例配置 (lru_cache 避免重复解析)."""
    return Settings()


# === FastAPI Dependencies ===
from fastapi import Depends  # noqa: E402

SettingsDep = Depends(get_settings)
