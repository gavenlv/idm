"""Service: 物理数据源接入 (CH / PG / BigQuery / GCS ...)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from idm_kg.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from idm_kg.models.database import Database


class Service(Base, UUIDMixin, TimestampMixin):
    """物理数据源, 例: 'clickhouse-prod' / 'superset-warehouse'."""

    __tablename__ = "services"

    name: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # clickhouse/postgres/gcs/...
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    tier: Mapped[str] = mapped_column(String(16), default="normal", nullable=False)
    # critical / important / normal (AGENT_INSTRUCTIONS §13)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    # active / deprecated / archived

    databases: Mapped[list["Database"]] = relationship(back_populates="service", cascade="all, delete-orphan")
