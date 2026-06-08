"""M1.5 真实数据管道: 给 table_assets 加 asset_subtype / external_ref, 给 table_lineage 加 transform_subtype.

设计见 docs/design/data-pipeline-lineage.md
- asset_subtype: 'gcs_object' | 'flink_table' | 'clickhouse_table' | 'airflow_dataset' | 'mex_io' | 'superset_dataset' | 'dbt_model' | ...
- external_ref: 外部系统定位符 (gcs://bucket/key / airflow://dag_id/task_id / ...)
- transform_subtype: 'flink_sql' | 'airflow_task' | 'mex_inference' | 'gcs_copy' | 'superset_query' | 'dbt_ref'
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0003_data_pipeline_lineage"
down_revision: str | Sequence[str] | None = "0002_data_quality_health"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # === table_assets 扩展 ===
    op.add_column(
        "table_assets",
        sa.Column("asset_subtype", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "table_assets",
        sa.Column("external_ref", sa.String(length=1024), nullable=True),
    )
    op.create_index(
        "ix_table_assets_asset_subtype",
        "table_assets",
        ["asset_subtype"],
    )

    # === table_lineage 扩展 ===
    op.add_column(
        "table_lineage",
        sa.Column("transform_subtype", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_table_lineage_transform_subtype",
        "table_lineage",
        ["transform_subtype"],
    )

    # === 新增: gcs_objects 表 (M1.5 真实管道) ===
    op.create_table(
        "gcs_objects",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("bucket", sa.String(length=256), nullable=False),
        sa.Column("key", sa.String(length=1024), nullable=False),
        sa.Column("fqn", sa.String(length=1280), nullable=False, unique=True),
        sa.Column("format", sa.String(length=32), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("row_count_estimate", sa.BigInteger(), nullable=True),
        sa.Column("schema_json", sa.JSON(), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_modified", sa.DateTime(timezone=True), nullable=True),
        sa.Column("profiled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
    )
    op.create_index("ix_gcs_objects_bucket", "gcs_objects", ["bucket"])
    op.create_index("ix_gcs_objects_fqn", "gcs_objects", ["fqn"], unique=True)

    # === 新增: pipelines 表 (DAG 维度) ===
    op.create_table(
        "pipelines",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=True),  # airflow_dag / flink_job / mex_model
        sa.Column("source_code_url", sa.String(length=1024), nullable=True),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
    )
    op.create_index("ix_pipelines_name", "pipelines", ["name"])
    op.create_index("ix_pipelines_type", "pipelines", ["type"])

    # === 新增: pipeline_runs 表 (一次执行) ===
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("pipeline_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("external_id", sa.String(length=256), nullable=True),  # Airflow run_id / Flink job_id
        sa.Column("status", sa.String(length=32), nullable=True),  # success / failed / running
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("input_rows", sa.BigInteger(), nullable=True),
        sa.Column("output_rows", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
    )
    op.create_index("ix_pipeline_runs_pipeline_id", "pipeline_runs", ["pipeline_id"])
    op.create_index("ix_pipeline_runs_status", "pipeline_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_runs_status", table_name="pipeline_runs")
    op.drop_index("ix_pipeline_runs_pipeline_id", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")

    op.drop_index("ix_pipelines_type", table_name="pipelines")
    op.drop_index("ix_pipelines_name", table_name="pipelines")
    op.drop_table("pipelines")

    op.drop_index("ix_gcs_objects_fqn", table_name="gcs_objects")
    op.drop_index("ix_gcs_objects_bucket", table_name="gcs_objects")
    op.drop_table("gcs_objects")

    op.drop_index("ix_table_lineage_transform_subtype", table_name="table_lineage")
    op.drop_column("table_lineage", "transform_subtype")

    op.drop_index("ix_table_assets_asset_subtype", table_name="table_assets")
    op.drop_column("table_assets", "external_ref")
    op.drop_column("table_assets", "asset_subtype")
