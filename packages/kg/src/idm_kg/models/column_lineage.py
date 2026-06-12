"""ColumnLineage: 列级血缘 (M2.x 新增).

设计: docs/design/data-model.md §7 + data-pipeline-lineage.md §4.3
- upstream_column -> downstream_column 的转换关系
- transform_type: direct / rename / cast / aggregation / expression / derivation / passthrough
- transform_expression: 源表达式原文 (e.g. "UPPER(name)", "SUM(amount)")
- component: airflow_task / flink_job / dbt_model / mex_model / sql / ai_inferred
- description: 组件级自然语言描述 (e.g. "由 user_id 转换为 UInt64 (用户 ID)")
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Index, SmallInteger, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from idm_kg.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from idm_kg.models.column_asset import ColumnAsset
    from idm_kg.models.table_asset import TableAsset


class ColumnLineage(Base, UUIDMixin, TimestampMixin):
    """列级血缘边: upstream_column -> downstream_column.

    同一对 (upstream, downstream, transform_type, job_id) 唯一.
    """

    __tablename__ = "column_lineage"
    __table_args__ = (
        UniqueConstraint(
            "upstream_column_id",
            "downstream_column_id",
            "transform_type",
            "job_id",
            name="uq_column_lineage_up_down_type_job",
        ),
        Index("idx_col_lineage_down_col", "downstream_column_id"),
        Index("idx_col_lineage_up_col", "upstream_column_id"),
        Index("idx_col_lineage_down_table", "downstream_table_id"),
        Index("idx_col_lineage_up_table", "upstream_table_id"),
        Index("idx_col_lineage_stage", "pipeline_stage"),
    )

    upstream_table_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("table_assets.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    downstream_table_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("table_assets.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    upstream_column_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("column_assets.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    downstream_column_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("column_assets.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    transform_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # direct | rename | cast | aggregation | expression | derivation | passthrough
    transform_expression: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # 源表达式原文, e.g. "UPPER(name)", "SUM(amount)", "orders.user_id"

    job_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    component: Mapped[str] = mapped_column(String(64), nullable=False, default="ai_inferred")
    # airflow_task | flink_job | dbt_model | mex_model | sql | ai_inferred | manual

    description: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # 列级自然语言描述
    description_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # manual | ai_inferred | imported

    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="sqlglot")
    # sqlglot / dbt_ref / flink_plan / ai_inferred / manual / lineage_to_column

    pipeline_stage: Mapped[int | None] = mapped_column(SmallInteger, nullable=True, index=True)
    # 1..6, 6 阶段管道标号

    # === OpenLineage 对齐 (M2.5): transformations JSONB ===
    # 对齐 OpenLineage ColumnLineageDatasetFacet.fields.<col>.transformations
    # 结构: [{"type": "DIRECT" | "TRANSFORMATION",
    #          "subtype": "SUM" | "CAST" | "UPPER" | ...,
    #          "description": "...",
    #          "expression": "SUM(amount) GROUP BY day",
    #          "masking": False}, ...]
    transformations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    extra: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # relations
    upstream_table: Mapped["TableAsset"] = relationship(
        foreign_keys=[upstream_table_id],
    )
    downstream_table: Mapped["TableAsset"] = relationship(
        foreign_keys=[downstream_table_id],
    )
    upstream_column: Mapped["ColumnAsset"] = relationship(
        foreign_keys=[upstream_column_id],
    )
    downstream_column: Mapped["ColumnAsset"] = relationship(
        foreign_keys=[downstream_column_id],
    )
