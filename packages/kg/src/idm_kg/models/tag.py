"""Tag & AssetTag: 业务标签 (manual / ai_inferred)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from idm_kg.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from idm_kg.models.table_asset import TableAsset


class Tag(Base, UUIDMixin, TimestampMixin):
    """标签字典, 例: 'pii', 'tier-1', 'sales', 'deprecated'."""

    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("name", name="uq_tags_name"),)

    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(32), default="custom", nullable=False)
    # pii / tier / domain / status / custom
    color: Mapped[str] = mapped_column(String(16), default="#999999", nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)

    assets: Mapped[list["AssetTag"]] = relationship(back_populates="tag", cascade="all, delete-orphan")


class AssetTag(Base, UUIDMixin, TimestampMixin):
    """多对多: table_asset <-> tag。"""

    __tablename__ = "asset_tags"
    __table_args__ = (UniqueConstraint("table_id", "tag_id", name="uq_asset_tags_table_tag"),)

    table_id: Mapped["uuid.UUID"] = mapped_column(ForeignKey("table_assets.id", ondelete="CASCADE"), index=True, nullable=False)  # type: ignore[name-defined]  # noqa: F821
    tag_id: Mapped["uuid.UUID"] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), index=True, nullable=False)  # type: ignore[name-defined]  # noqa: F821
    source: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    # manual / ai_inferred / policy

    table: Mapped["TableAsset"] = relationship(back_populates="tags")
    tag: Mapped["Tag"] = relationship(back_populates="assets")
