"""M1.5 Data Quality 增量: 给 table_assets 加 health_score / health_score_updated_at.

设计见 AGENT_INSTRUCTIONS.md §M1.5 (Data Quality 优先级提前)。
- detect_anomalies skill 在每次跑完后回写 health_score (0-100, 越低越异常)
- QualityPage UI 用此字段做 Top-N 低分表 + 整库均值
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0002_data_quality_health"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "table_assets",
        sa.Column("health_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "table_assets",
        sa.Column("health_score_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_table_assets_health_score",
        "table_assets",
        ["health_score"],
    )


def downgrade() -> None:
    op.drop_index("ix_table_assets_health_score", table_name="table_assets")
    op.drop_column("table_assets", "health_score_updated_at")
    op.drop_column("table_assets", "health_score")
