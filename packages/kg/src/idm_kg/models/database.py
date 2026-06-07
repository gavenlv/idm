"""Database: 逻辑数据库 (CH 中叫 'database'; PG 中类似 'database'/'schema')."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from idm_kg.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from idm_kg.models.service import Service
    from idm_kg.models.schema import Schema


class Database(Base, UUIDMixin, TimestampMixin):
    """逻辑数据库, 在 Service 下。"""

    __tablename__ = "databases"
    __table_args__ = (UniqueConstraint("service_id", "name", name="uq_databases_service_name"),)

    service_id: Mapped["uuid.UUID"] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"), index=True, nullable=False)  # type: ignore[name-defined]  # noqa: F821
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # description_vec / description_tsv 在 M1 S1.2 加 pgvector / pg_trgm

    service: Mapped["Service"] = relationship(back_populates="databases")
    schemas: Mapped[list["Schema"]] = relationship(back_populates="database", cascade="all, delete-orphan")
