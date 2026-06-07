"""Schema: 命名空间 / DB-schema (PG 强, CH 弱)."""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from idm_kg.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from idm_kg.models.database import Database
    from idm_kg.models.table_asset import TableAsset


class Schema(Base, UUIDMixin, TimestampMixin):
    """Schema (命名空间), 隶属 Database。"""

    __tablename__ = "schemas"
    __table_args__ = (UniqueConstraint("database_id", "name", name="uq_schemas_database_name"),)

    database_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("databases.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    database: Mapped["Database"] = relationship(back_populates="schemas")
    tables: Mapped[list["TableAsset"]] = relationship(back_populates="schema_", cascade="all, delete-orphan")
