"""Alembic env: 用 idm_api.config 注入 DSN, 用 idm_kg.models.Base.metadata 作 target."""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# === 把 apps/api/src 加进 sys.path (兜底, 通常 prepend_sys_path 已配) ===
API_SRC = Path(__file__).resolve().parent.parent / "apps" / "api" / "src"
if str(API_SRC) not in sys.path:
    sys.path.insert(0, str(API_SRC))

# === 导入 Base + 所有 model ===
from idm_kg import Base  # noqa: E402
from idm_api.config import get_settings  # noqa: E402

# Alembic Config
config = context.config

# 把 settings 里的同步 DSN 注入
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url_sync)

# 解析 .ini 的 logger config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 目标 metadata
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """无 DB 连接模式 (--sql 模式)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """DB 连接模式."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
