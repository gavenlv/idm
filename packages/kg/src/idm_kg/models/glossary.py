"""GlossaryTerm & AssetTerm: 业务术语 + 资产绑定。"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from idm_kg.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from idm_kg.models.table_asset import TableAsset


class GlossaryTerm(Base, UUIDMixin, TimestampMixin):
    """业务术语字典, 由 Glossary Agent 维护。"""

    __tablename__ = "glossary_terms"
    __table_args__ = (UniqueConstraint("name", name="uq_glossary_terms_name"),)

    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    definition: Mapped[str] = mapped_column(String(2048), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # sales / finance / risk / ops
    owner_team: Mapped[str | None] = mapped_column(String(128), nullable=True)
    synonyms: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    assets: Mapped[list["AssetTerm"]] = relationship(back_populates="term", cascade="all, delete-orphan")


class AssetTerm(Base, UUIDMixin, TimestampMixin):
    """术语 <-> 资产 多对多。"""

    __tablename__ = "asset_terms"
    __table_args__ = (UniqueConstraint("table_id", "term_id", name="uq_asset_terms_table_term"),)

    table_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("table_assets.id", ondelete="CASCADE"), index=True, nullable=False)
    term_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("glossary_terms.id", ondelete="CASCADE"), index=True, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="ai_inferred", nullable=False)

    table: Mapped["TableAsset"] = relationship(back_populates="terms")
    term: Mapped["GlossaryTerm"] = relationship(back_populates="assets")
