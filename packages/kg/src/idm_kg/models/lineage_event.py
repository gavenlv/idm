"""LineageEvent: OpenLineage 兼容的事件流 (M2.5 新增).

设计: docs/design/openlineage-alignment.md
- 对齐 OpenLineage 1.0+ RunEvent 规范 (https://openlineage.io/spec/)
- append-only 审计表
- 用于 export 到 Marquez / DataHub / 其他 OL 兼容后端
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from idm_kg.models.base import Base, TimestampMixin, UUIDMixin


class LineageEvent(Base, UUIDMixin, TimestampMixin):
    """OpenLineage-compatible RunEvent.

    存储 IDM 内部产出的血缘事件, 可:
    1. 审计: 谁 (skill) 什么时间 (event_time) 跑了什么 (job) 产生了什么 (inputs/outputs)
    2. 互操作: export 为 OpenLineage JSON, 推送到 Marquez / DataHub
    """

    __tablename__ = "lineage_event"
    __table_args__ = (
        Index("ix_lineage_event_job", "job_namespace", "job_name", "event_time"),
        Index("ix_lineage_event_run", "run_id"),
        Index("ix_lineage_event_type", "event_type"),
        Index("ix_lineage_event_time", "event_time"),
    )

    # OpenLineage 核心字段
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # START | RUNNING | COMPLETE | FAIL | ABORT  (对齐 OL lifecycle)

    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    job_namespace: Mapped[str] = mapped_column(String(256), nullable=False)
    # e.g. "airflow-prod" / "flink-cluster" / "dbt-cloud" / "idm://shop-orders-mex-pipeline"

    job_name: Mapped[str] = mapped_column(String(256), nullable=False)
    # e.g. "etl_orders_daily" / "load_to_clickhouse" / "mex_orders_risk"

    run_id: Mapped[str] = mapped_column(String(256), nullable=False)
    # OpenLineage Run.runId (e.g. "scheduled__2026-06-12T01:00:00+00:00")

    # OpenLineage inputs/outputs: List of {namespace, name, facets}
    inputs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    outputs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # Run-level facets: parent / processing_engine / environment / ...
    facets: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # IDM 扩展字段
    producer: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # e.g. "idm/0.4.0" / "idm-skill/emit_openlineage_event"

    source_skill: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 哪个 IDM skill 触发的 (e.g. "emit_openlineage_event" / "analyze_data_pipeline")

    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    extra: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
