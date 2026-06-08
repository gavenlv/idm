"""M1.5 强化: 给 pipelines / gcs_objects / table_assets / table_lineage 加 pipeline_stage 列.

设计见 docs/design/data-pipeline-lineage.md §4.2
- pipeline_stage SMALLINT  (1|2|3|4|5|6, 强约束, 用于 6 阶段真实管道用例)
- pipelines 加 stage (M1.5 强化, 让 Pipeline.type 联合 stage 唯一约束)
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0004_pipeline_stage"
down_revision: str | Sequence[str] | None = "0003_data_pipeline_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # === pipelines 加 stage ===
    op.add_column(
        "pipelines",
        sa.Column("stage", sa.SmallInteger(), nullable=True),
    )
    op.create_index("ix_pipelines_stage", "pipelines", ["stage"])

    # === gcs_objects 加 pipeline_stage ===
    op.add_column(
        "gcs_objects",
        sa.Column("pipeline_stage", sa.SmallInteger(), nullable=True),
    )
    op.create_index("ix_gcs_objects_stage", "gcs_objects", ["pipeline_stage"])

    # === table_assets 加 pipeline_stage ===
    op.add_column(
        "table_assets",
        sa.Column("pipeline_stage", sa.SmallInteger(), nullable=True),
    )
    op.create_index(
        "ix_table_assets_pipeline_stage",
        "table_assets",
        ["pipeline_stage"],
    )

    # === table_lineage 加 pipeline_stage ===
    op.add_column(
        "table_lineage",
        sa.Column("pipeline_stage", sa.SmallInteger(), nullable=True),
    )
    op.create_index(
        "ix_table_lineage_pipeline_stage",
        "table_lineage",
        ["pipeline_stage"],
    )


def downgrade() -> None:
    op.drop_index("ix_table_lineage_pipeline_stage", table_name="table_lineage")
    op.drop_column("table_lineage", "pipeline_stage")

    op.drop_index("ix_table_assets_pipeline_stage", table_name="table_assets")
    op.drop_column("table_assets", "pipeline_stage")

    op.drop_index("ix_gcs_objects_stage", table_name="gcs_objects")
    op.drop_column("gcs_objects", "pipeline_stage")

    op.drop_index("ix_pipelines_stage", table_name="pipelines")
    op.drop_column("pipelines", "stage")
