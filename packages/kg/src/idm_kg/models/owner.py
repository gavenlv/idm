"""AssetOwner: 表的 Owner / Steward / Consumer (3 种角色)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from idm_kg.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from idm_kg.models.table_asset import TableAsset


class AssetOwner(Base, UUIDMixin, TimestampMixin):
    """表的所有者, 由 Owner Agent 推断 + 人工确认。"""

    __tablename__ = "asset_owners"
    __table_args__ = (UniqueConstraint("table_id", "user_email", "role", name="uq_owners_table_user_role"),)

    table_id: Mapped["uuid.UUID"] = mapped_column(ForeignKey("table_assets.id", ondelete="CASCADE"), index=True, nullable=False)  # type: ignore[name-defined]  # noqa: F821
    user_email: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    user_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    team: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(32), default="owner", nullable=False)
    # owner / steward / consumer
    source: Mapped[str] = mapped_column(String(32), default="ai_inferred", nullable=False)
    # git_blame / dbt_meta / airflow_owner / ai_inferred / manual
    confidence: Mapped[float] = mapped_column(default=1.0, nullable=False)
    is_verified: Mapped[bool] = mapped_column(default=False, nullable=False)

    table: Mapped["TableAsset"] = relationship(back_populates="owners")
