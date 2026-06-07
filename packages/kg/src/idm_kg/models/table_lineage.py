"""TableLineage: 血缘边 (由 Lineage Agent + parse_* Skills 产生)."""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from idm_kg.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from idm_kg.models.table_asset import TableAsset


class TableLineage(Base, UUIDMixin, TimestampMixin):
    """血缘边: upstream -> downstream。

    transform_type: copy / aggregation / dbt_model / airflow_task / superset_chart / sql
    job_id: 产生此血缘的 Pipeline / DAG 任务 ID
    """

    __tablename__ = "table_lineage"
    __table_args__ = (
        UniqueConstraint("upstream_id", "downstream_id", "transform_type", name="uq_lineage_up_down_type"),
    )

    upstream_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("table_assets.id", ondelete="CASCADE"), index=True, nullable=False)
    downstream_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("table_assets.id", ondelete="CASCADE"), index=True, nullable=False)
    transform_type: Mapped[str] = mapped_column(String(32), default="copy", nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    sql: Mapped[str | None] = mapped_column(String(8192), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="ai_inferred", nullable=False)
    # dbt_manifest / airflow_dag / superset_export / sqlglot / ai_inferred
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    upstream_table: Mapped["TableAsset"] = relationship(
        foreign_keys=[upstream_id], back_populates="downstream"
    )
    downstream_table: Mapped["TableAsset"] = relationship(
        foreign_keys=[downstream_id], back_populates="upstream"
    )
