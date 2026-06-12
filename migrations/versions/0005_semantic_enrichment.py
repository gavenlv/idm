"""M2.x Semantic Enrichment: 列级血缘 + 资产/列/血缘边 description 字段.

设计: docs/design/data-model.md §7 + data-pipeline-lineage.md §4.3
- 新表 column_lineage (列级血缘)
- table_assets 加 description_rationale / described_at
- column_assets 加 description_source / description_rationale
- table_lineage 加 component / transform_subtype / transform_expression / description / description_source / description_rationale
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "0005_semantic_enrichment"
down_revision: str | Sequence[str] | None = "0004_pipeline_stage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # === table_assets 加 description_rationale / described_at ===
    op.add_column(
        "table_assets",
        sa.Column("description_rationale", sa.String(length=2048), nullable=True),
    )
    op.add_column(
        "table_assets",
        sa.Column("described_at", sa.DateTime(timezone=True), nullable=True),
    )

    # === column_assets 加 description_source / description_rationale ===
    op.add_column(
        "column_assets",
        sa.Column("description_source", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "column_assets",
        sa.Column("description_rationale", sa.String(length=2048), nullable=True),
    )

    # === table_lineage 加 component / transform_expression / description / description_source / description_rationale ===
    # 注: transform_subtype 在 0003 已加
    op.add_column(
        "table_lineage",
        sa.Column("transform_expression", sa.String(length=2048), nullable=True),
    )
    op.add_column(
        "table_lineage",
        sa.Column("component", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "table_lineage",
        sa.Column("description", sa.String(length=2048), nullable=True),
    )
    op.add_column(
        "table_lineage",
        sa.Column("description_source", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "table_lineage",
        sa.Column("description_rationale", sa.String(length=1024), nullable=True),
    )
    op.create_index("ix_table_lineage_component", "table_lineage", ["component"])

    # === 新表 column_lineage (列级血缘) ===
    op.create_table(
        "column_lineage",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("upstream_table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("downstream_table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("upstream_column_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("downstream_column_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transform_type", sa.String(length=32), nullable=False),
        sa.Column("transform_expression", sa.String(length=2048), nullable=True),
        sa.Column("job_id", sa.String(length=256), nullable=True),
        sa.Column("component", sa.String(length=64), nullable=False, server_default="ai_inferred"),
        sa.Column("description", sa.String(length=2048), nullable=True),
        sa.Column("description_source", sa.String(length=32), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="sqlglot"),
        sa.Column("pipeline_stage", sa.SmallInteger(), nullable=True),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint(
            "upstream_column_id", "downstream_column_id", "transform_type", "job_id",
            name="uq_column_lineage_up_down_type_job",
        ),
        sa.ForeignKeyConstraint(["upstream_table_id"], ["table_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["downstream_table_id"], ["table_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["upstream_column_id"], ["column_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["downstream_column_id"], ["column_assets.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_col_lineage_down_col", "column_lineage", ["downstream_column_id"])
    op.create_index("idx_col_lineage_up_col", "column_lineage", ["upstream_column_id"])
    op.create_index("idx_col_lineage_down_table", "column_lineage", ["downstream_table_id"])
    op.create_index("idx_col_lineage_up_table", "column_lineage", ["upstream_table_id"])
    op.create_index("idx_col_lineage_stage", "column_lineage", ["pipeline_stage"])


def downgrade() -> None:
    # === drop column_lineage ===
    op.drop_index("idx_col_lineage_stage", table_name="column_lineage")
    op.drop_index("idx_col_lineage_up_table", table_name="column_lineage")
    op.drop_index("idx_col_lineage_down_table", table_name="column_lineage")
    op.drop_index("idx_col_lineage_up_col", table_name="column_lineage")
    op.drop_index("idx_col_lineage_down_col", table_name="column_lineage")
    op.drop_table("column_lineage")

    # === drop table_lineage added columns ===
    op.drop_index("ix_table_lineage_component", table_name="table_lineage")
    op.drop_column("table_lineage", "description_rationale")
    op.drop_column("table_lineage", "description_source")
    op.drop_column("table_lineage", "description")
    op.drop_column("table_lineage", "component")
    op.drop_column("table_lineage", "transform_expression")
    # 注: transform_subtype 在 0003 加, 不在 0005 删

    # === drop column_assets added columns ===
    op.drop_column("column_assets", "description_rationale")
    op.drop_column("column_assets", "description_source")

    # === drop table_assets added columns ===
    op.drop_column("table_assets", "described_at")
    op.drop_column("table_assets", "description_rationale")
