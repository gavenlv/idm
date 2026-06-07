"""QualityRule & QualityResult: 质量断言 + 时序结果。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from idm_kg.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from idm_kg.models.table_asset import TableAsset


class QualityRule(Base, UUIDMixin, TimestampMixin):
    """质量规则, 例: row_count > 0 / freshness < 1h / volume_anomaly < 3sigma。"""

    __tablename__ = "quality_rules"
    __table_args__ = (UniqueConstraint("table_id", "name", name="uq_quality_rules_table_name"),)

    table_id: Mapped["uuid.UUID"] = mapped_column(ForeignKey("table_assets.id", ondelete="CASCADE"), index=True, nullable=False)  # type: ignore[name-defined]  # noqa: F821
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # freshness / volume / null_ratio / distinct / custom / anomaly
    severity: Mapped[str] = mapped_column(String(16), default="warning", nullable=False)
    # info / warning / critical
    definition: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    schedule: Mapped[str] = mapped_column(String(64), default="0 * * * *", nullable=False)
    is_enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    results: Mapped[list["QualityResult"]] = relationship(back_populates="rule", cascade="all, delete-orphan")


class QualityResult(Base, UUIDMixin, TimestampMixin):
    """规则执行结果 (时序)。"""

    __tablename__ = "quality_results"

    rule_id: Mapped["uuid.UUID"] = mapped_column(ForeignKey("quality_rules.id", ondelete="CASCADE"), index=True, nullable=False)  # type: ignore[name-defined]  # noqa: F821
    passed: Mapped[bool] = mapped_column(nullable=False)
    observed_value: Mapped[float | None] = mapped_column(nullable=True)
    threshold: Mapped[float | None] = mapped_column(nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    rule: Mapped["QualityRule"] = relationship(back_populates="results")
