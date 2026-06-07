"""ColumnAsset: 表内列 (含 PII 分类、描述、tag)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from idm_kg.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from idm_kg.models.table_asset import TableAsset


class ColumnAsset(Base, UUIDMixin, TimestampMixin):
    """列资产, PII 分类由 PII Agent (gpt-5 / qwen-local) 推断后写入 ai_suggestion。"""

    __tablename__ = "column_assets"
    __table_args__ = (
        UniqueConstraint("table_id", "name", name="uq_column_assets_table_name"),
    )

    table_id: Mapped["uuid.UUID"] = mapped_column(ForeignKey("table_assets.id", ondelete="CASCADE"), index=True, nullable=False)  # type: ignore[name-defined]  # noqa: F821
    name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    data_type: Mapped[str] = mapped_column(String(64), nullable=False)
    nullable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_primary_key: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_partition_key: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # 业务
    description: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    pii_class: Mapped[str] = mapped_column(String(32), default="none", nullable=False)
    # none / email / phone / id_card / address / name / card_bin / ip / ...
    pii_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    pii_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # manual / ai_inferred / regex / pattern

    # 样本
    sample_values: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    null_ratio: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    distinct_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    table: Mapped["TableAsset"] = relationship(back_populates="columns")
