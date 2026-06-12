"""M2.5 OpenLineage Alignment: lineage_event 表 + column_lineage.transformations + table_assets.ol_namespace.

设计: docs/design/openlineage-alignment.md

参考业界标准: https://openlineage.io/

3 处变更 (不破坏 M2.x):
1. 新表 `lineage_event` — 审计 + OpenLineage-compatible 事件流 (append-only)
2. `column_lineage.transformations JSONB` — 对齐 OpenLineage ColumnLineageDatasetFacet
3. `table_assets.ol_namespace TEXT` — 对齐 OpenLineage Dataset.namespace 概念
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "0006_openlineage_alignment"
down_revision: str | Sequence[str] | None = "0005_semantic_enrichment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # === 1. 新表 lineage_event ===
    op.create_table(
        "lineage_event",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        # OpenLineage lifecycle: START | RUNNING | COMPLETE | FAIL | ABORT
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("job_namespace", sa.String(length=256), nullable=False),
        # e.g. "airflow-prod" / "flink-cluster" / "dbt-cloud"
        sa.Column("job_name", sa.String(length=256), nullable=False),
        # e.g. "etl_orders_daily" / "load_to_clickhouse"
        sa.Column("run_id", sa.String(length=256), nullable=False),
        # OpenLineage Run.runId
        sa.Column("inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        # [{namespace, name, facets: {...}}, ...]
        sa.Column("outputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        # [{namespace, name, facets: {...}}, ...]
        sa.Column("facets", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        # run facets: parent / processing_engine / environment / ...
        sa.Column("producer", sa.String(length=64), nullable=True),
        # e.g. "idm/0.4.0" / "idm-skill/emit_openlineage_event"
        sa.Column("source_skill", sa.String(length=64), nullable=True),
        # 哪个 IDM skill 触发的 (e.g. "emit_openlineage_event" / "analyze_data_pipeline")
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        # 引用 pipeline_run.id (外键在后续迁移加, 这里先不绑, 避免循环依赖)
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_lineage_event_job", "lineage_event", ["job_namespace", "job_name", "event_time"])
    op.create_index("ix_lineage_event_run", "lineage_event", ["run_id"])
    op.create_index("ix_lineage_event_type", "lineage_event", ["event_type"])
    op.create_index("ix_lineage_event_time", "lineage_event", ["event_time"])

    # === 2. column_lineage 加 transformations JSONB (对齐 OL ColumnLineageDatasetFacet) ===
    op.add_column(
        "column_lineage",
        sa.Column(
            "transformations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    # 结构: [{"type": "DIRECT" | "TRANSFORMATION", "subtype": "SUM" | "CAST" | ...,
    #         "description": "...", "expression": "SUM(amount) GROUP BY day"}, ...]
    # 100% 对齐 OpenLineage ColumnLineageDatasetFacet.fields.<col>.transformations

    # === 3. table_assets 加 ol_namespace (对齐 OL Dataset.namespace) ===
    op.add_column(
        "table_assets",
        sa.Column("ol_namespace", sa.String(length=256), nullable=True),
    )
    # e.g. "clickhouse://shop" / "gcs://company-raw" / "bigquery://project-id"
    op.create_index("ix_table_assets_ol_ns", "table_assets", ["ol_namespace"])


def downgrade() -> None:
    # === 3 ===
    op.drop_index("ix_table_assets_ol_ns", table_name="table_assets")
    op.drop_column("table_assets", "ol_namespace")

    # === 2 ===
    op.drop_column("column_lineage", "transformations")

    # === 1 ===
    op.drop_index("ix_lineage_event_time", table_name="lineage_event")
    op.drop_index("ix_lineage_event_type", table_name="lineage_event")
    op.drop_index("ix_lineage_event_run", table_name="lineage_event")
    op.drop_index("ix_lineage_event_job", table_name="lineage_event")
    op.drop_table("lineage_event")
