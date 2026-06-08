"""GcsObject: GCS 上的数据文件作为"资产" (M1.5 真实管道).

每个 parquet/csv/json 文件 = 一行, 同时通过 fqn 在 table_assets 里建对应虚表 (asset_subtype='gcs_object')。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Index, SmallInteger, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from idm_kg.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    pass


class GcsObject(Base, UUIDMixin, TimestampMixin):
    """GCS 对象元数据 + schema 推断结果."""

    __tablename__ = "gcs_objects"
    __table_args__ = (
        Index("ix_gcs_objects_fqn", "fqn", unique=True),
        Index("ix_gcs_objects_bucket", "bucket"),
        Index("ix_gcs_objects_stage", "pipeline_stage"),
    )

    bucket: Mapped[str] = mapped_column(String(256), nullable=False)
    key: Mapped[str] = mapped_column(String(1024), nullable=False)
    fqn: Mapped[str] = mapped_column(String(1280), nullable=False)
    # parquet / csv / json / orc / avro
    format: Mapped[str | None] = mapped_column(String(32), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 估算行数 (parquet metadata 或 CSV 行数 * file size)
    row_count_estimate: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 推断的列 schema: [{name, type, nullable}, ...]
    schema_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 6 阶段管道标号: 1=上游, 2=model-input, 4=model-output
    pipeline_stage: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_modified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    profiled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "bucket": self.bucket,
            "key": self.key,
            "fqn": self.fqn,
            "format": self.format,
            "size_bytes": self.size_bytes,
            "row_count_estimate": self.row_count_estimate,
            "schema": self.schema_json or [],
            "pipeline_stage": self.pipeline_stage,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_modified": self.last_modified.isoformat() if self.last_modified else None,
            "profiled_at": self.profiled_at.isoformat() if self.profiled_at else None,
        }


class Pipeline(Base, UUIDMixin, TimestampMixin):
    """Pipeline (DAG 维度) — Airflow DAG / Flink Job / dbt Model / Superset Refresh / MEX 模型 / 等等."""

    __tablename__ = "pipelines"
    __table_args__ = (
        Index("ix_pipelines_name", "name"),
        Index("ix_pipelines_type", "type"),
        Index("ix_pipelines_stage", "stage"),
    )

    name: Mapped[str] = mapped_column(String(256), nullable=False)
    # airflow_dag / flink_job / mex_model / superset_refresh / dbt_project
    type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 6 阶段管道标号: 1|2|3|4|5|6 (强约束; airflow_dag=1, flink_job=1|5, mex_model=3, superset_refresh=6)
    stage: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # GitHub URL of the source code
    source_code_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    description: Mapped[str | None] = mapped_column(String(4096), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "name": self.name,
            "type": self.type,
            "stage": self.stage,
            "source_code_url": self.source_code_url,
            "config": self.config or {},
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class PipelineRun(Base, UUIDMixin):
    """一次 Pipeline 执行 (Airflow DAG run / Flink Job run)."""

    __tablename__ = "pipeline_runs"
    __table_args__ = (
        Index("ix_pipeline_runs_pipeline_id", "pipeline_id"),
        Index("ix_pipeline_runs_status", "status"),
    )

    # 引用 Pipeline (不强约束, 留 nullable 兼容未注册的 pipeline)
    pipeline_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    # Airflow run_id / Flink job_id
    external_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # success / failed / running
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    input_rows: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_rows: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error: Mapped[str | None] = mapped_column(nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default="now()"
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "pipeline_id": str(self.pipeline_id) if self.pipeline_id else None,
            "external_id": self.external_id,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_ms": self.duration_ms,
            "input_rows": self.input_rows,
            "output_rows": self.output_rows,
            "error": self.error,
        }
