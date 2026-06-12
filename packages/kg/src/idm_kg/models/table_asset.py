"""TableAsset: 核心资产 (表 / 视图 / 物化视图 / dbt model)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer, SmallInteger, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from idm_kg.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from idm_kg.models.column_asset import ColumnAsset
    from idm_kg.models.glossary import AssetTerm
    from idm_kg.models.owner import AssetOwner
    from idm_kg.models.schema import Schema
    from idm_kg.models.table_lineage import TableLineage
    from idm_kg.models.tag import AssetTag


class TableAsset(Base, UUIDMixin, TimestampMixin):
    """表/视图资产。

    FQN = <service>.<database>.<schema>.<table> (AGENT_INSTRUCTIONS §13)。
    """

    __tablename__ = "table_assets"
    __table_args__ = (
        UniqueConstraint("schema_id", "name", name="uq_table_assets_schema_name"),
        UniqueConstraint("fqn", name="uq_table_assets_fqn"),
    )

    schema_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schemas.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    fqn: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    asset_type: Mapped[str] = mapped_column(String(32), default="table", nullable=False)
    # table / view / materialized_view / dbt_model / dashboard
    tier: Mapped[str] = mapped_column(String(16), default="normal", nullable=False)
    # critical / important / normal
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    # active / deprecated / archived

    # 业务信息 (LLM 推断)
    description: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    description_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # manual / ai_inferred / imported
    description_rationale: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # LLM 推理过程 / 用户备注
    described_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_profiled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 统计
    column_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    row_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_query_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    query_count_30d: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # === Data Quality (M4) ===
    # 综合健康分 0-100, 由 detect_anomalies 写入; 越低越异常
    health_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 上次计算 health_score 的时间
    health_score_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # === 真实数据管道 (M1.5) ===
    # 子类型: gcs_object / flink_table / airflow_dataset / mex_io / superset_dataset / dbt_model / clickhouse_table
    asset_subtype: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # 外部系统定位符: gcs://bucket/key / airflow://dag_id/task_id / flink://db/table / mex://model_name
    external_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # 6 阶段管道标号: 1|2|3|4|5|6 (强约束; 用于 6 阶段真实管道用例)
    pipeline_stage: Mapped[int | None] = mapped_column(SmallInteger, nullable=True, index=True)

    # === OpenLineage 对齐 (M2.5): ol_namespace ===
    # 对齐 OpenLineage Dataset.namespace (e.g. "clickhouse://shop" / "gcs://company-raw")
    # 默认 = service.database 拼接, 也可手动指定 (如跨 service 聚合场景)
    ol_namespace: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)

    # 扩展属性 (PII / 业务标签 / 备注)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # relations
    schema_: Mapped["Schema"] = relationship(back_populates="tables", foreign_keys=[schema_id])
    columns: Mapped[list["ColumnAsset"]] = relationship(back_populates="table", cascade="all, delete-orphan")
    owners: Mapped[list["AssetOwner"]] = relationship(back_populates="table", cascade="all, delete-orphan")
    tags: Mapped[list["AssetTag"]] = relationship(back_populates="table", cascade="all, delete-orphan")
    terms: Mapped[list["AssetTerm"]] = relationship(back_populates="table", cascade="all, delete-orphan")
    upstream: Mapped[list["TableLineage"]] = relationship(
        back_populates="downstream_table",
        foreign_keys="TableLineage.downstream_id",
        cascade="all, delete-orphan",
    )
    downstream: Mapped[list["TableLineage"]] = relationship(
        back_populates="upstream_table",
        foreign_keys="TableLineage.upstream_id",
        cascade="all, delete-orphan",
    )
