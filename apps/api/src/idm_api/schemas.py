"""Pydantic Schemas (请求/响应 DTO)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# === Service ===
class ServiceBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    type: str = Field(..., max_length=32)
    description: str | None = Field(None, max_length=1024)
    config: dict[str, Any] = Field(default_factory=dict)
    tier: Literal["critical", "important", "normal"] = "normal"
    status: Literal["active", "deprecated", "archived"] = "active"


class ServiceCreate(ServiceBase):
    pass


class ServiceRead(ServiceBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


# === TableAsset ===
class TableAssetBase(BaseModel):
    name: str = Field(..., max_length=256)
    # FQN 接受两种格式:
    #  - 业务标识:  clickhouse-prod.shop.default.orders_daily (service.db.schema.table)
    #  - URI 风格:  gcs://bucket/path/to/object.csv        (GCS / S3 / external objects)
    # 未来可加 http://, s3://, bigquery://, k8s:// ... 任何 `proto://...` 形式
    fqn: str = Field(..., max_length=512, pattern=r"^[a-z0-9_.:/-]+$")
    asset_type: Literal[
        "table", "view", "materialized_view", "dbt_model",
        "dashboard", "superset_dashboard", "superset_chart", "superset_dataset",
    ] = "table"
    tier: Literal["critical", "important", "normal"] = "normal"
    status: Literal["active", "deprecated", "archived"] = "active"
    description: str | None = Field(None, max_length=4096)
    extra: dict[str, Any] = Field(default_factory=dict)


class TableAssetCreate(TableAssetBase):
    schema_id: UUID
    service_name: str  # 简化: 创建时直接给 fqn 已包含 service, 这里只给 schema_id


class TableAssetRead(TableAssetBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    schema_id: UUID
    column_count: int
    row_count: int | None
    size_bytes: int | None
    # === Data Quality (M4) ===
    health_score: float | None = None
    health_score_updated_at: datetime | None = None
    # === M2.x Semantic Enrichment ===
    description_source: str | None = None
    description_rationale: str | None = None
    described_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TableAssetListResponse(BaseModel):
    items: list[TableAssetRead]
    total: int
    limit: int
    offset: int


# === ColumnAsset ===
class ColumnAssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    table_id: UUID
    name: str
    ordinal: int
    data_type: str
    nullable: bool
    is_primary_key: bool
    is_partition_key: bool
    description: str | None
    pii_class: str
    pii_confidence: float
    pii_source: str | None
    sample_values: list[Any]
    null_ratio: float
    distinct_count: int | None
    created_at: datetime
    updated_at: datetime


class ColumnAssetListResponse(BaseModel):
    items: list[ColumnAssetRead]
    total: int


class AssetPiiSummary(BaseModel):
    """一张表的 PII 风险摘要."""

    table_id: UUID
    pii_columns: int
    high_risk_columns: int
    by_class: dict[str, int]
    samples: list[dict[str, Any]]  # [{column_name, pii_class, confidence}, ...]


# === TableLineage ===
class LineageEdgeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    upstream_id: UUID
    downstream_id: UUID
    transform_type: str
    job_id: str | None
    confidence: float
    source: str
    upstream_fqn: str | None = None
    downstream_fqn: str | None = None
    # === M2.x 新增 ===
    transform_subtype: str | None = None
    transform_expression: str | None = None
    component: str | None = None
    description: str | None = None
    description_source: str | None = None
    pipeline_stage: int | None = None


class LineageGraphResponse(BaseModel):
    """以一张表为中心的 lineage 视图 (depth=BFS 上/下游 N 层)."""

    center_fqn: str
    center_id: UUID
    upstream: list[LineageEdgeRead]  # 边列表, 端点 fqn 已展开
    downstream: list[LineageEdgeRead]
    nodes: list[dict[str, Any]]  # [{id, fqn, asset_type, tier}, ...] 去重
    edges: list[LineageEdgeRead]


# === ColumnLineage (M2.x) ===
class ColumnLineageEdgeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    upstream_table_id: UUID
    downstream_table_id: UUID
    upstream_column_id: UUID
    downstream_column_id: UUID
    transform_type: str
    transform_expression: str | None = None
    job_id: str | None = None
    component: str
    description: str | None = None
    description_source: str | None = None
    confidence: float
    source: str
    pipeline_stage: int | None = None
    # 展开字段 (非存储)
    upstream_table_fqn: str | None = None
    downstream_table_fqn: str | None = None
    upstream_column_name: str | None = None
    downstream_column_name: str | None = None
    upstream_column_type: str | None = None
    downstream_column_type: str | None = None


class ColumnLineageResponse(BaseModel):
    """列级血缘响应: 以某列 (或某表) 为中心."""

    center_table_id: UUID | None = None
    center_column_id: UUID | None = None
    upstream: list[ColumnLineageEdgeRead]
    downstream: list[ColumnLineageEdgeRead]
    total: int


class ColumnLineageStatsResponse(BaseModel):
    n_edges: int
    n_transform_types: dict[str, int]
    n_components: dict[str, int]
    coverage: dict[str, int]  # {table_with_col_lineage: count}


# === Column Lineage Coverage (M2.5+) ===
class ColumnCoverageEntry(BaseModel):
    """单列的列血缘覆盖状态."""

    column_id: UUID
    column_name: str
    data_type: str
    has_upstream: bool
    has_downstream: bool
    n_upstream_edges: int
    n_downstream_edges: int


class TableColumnCoverage(BaseModel):
    """单表的列血缘覆盖统计."""

    table_id: UUID
    table_fqn: str
    asset_type: str
    tier: str
    n_columns: int
    n_columns_with_lineage: int
    coverage_pct: float  # 0-100
    has_table_lineage: bool
    n_table_lineage_edges: int
    columns: list[ColumnCoverageEntry]


class ColumnCoverageResponse(BaseModel):
    """全表列血缘覆盖响应 (OpenLineage-style coverage matrix)."""

    total_tables: int
    total_columns: int
    total_columns_with_lineage: int
    overall_coverage_pct: float
    tables: list[TableColumnCoverage]


class BulkInferRequest(BaseModel):
    """批量列血缘推断请求."""

    table_ids: list[UUID] | None = None
    # None = all tables; or specific list
    include_table_lineage_inference: bool = True
    # If True, run lineage_reasoner first (for tables without table_lineage)
    include_column_lineage_inference: bool = True
    # If True, run infer_column_lineage for sql-driven cases
    include_lineage_to_column: bool = True
    # If True, run lineage_to_column for table_lineage edges
    min_confidence: float = 0.5
    dry_run: bool = False


class BulkInferResponse(BaseModel):
    """批量列血缘推断响应."""

    ok: bool
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    tables_processed: int
    tables_skipped: int
    table_lineage_edges_created: int
    column_lineage_edges_created: int
    errors: list[str]
    dry_run: bool
    summary: dict[str, Any] = Field(default_factory=dict)


# === OpenLineage (M2.5) ===
class OpenLineageEventRead(BaseModel):
    """OpenLineage-compatible 事件读模型.

    内部存储 + 外部互操作的统一表示。
    详见: docs/design/openlineage-alignment.md
    """
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: str
    event_time: datetime
    job_namespace: str
    job_name: str
    run_id: str
    inputs: list[dict[str, Any]] = Field(default_factory=list)
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    facets: dict[str, Any] = Field(default_factory=dict)
    producer: str | None = None
    source_skill: str | None = None
    pipeline_run_id: UUID | None = None
    # 完整 OpenLineage RunEvent JSON (与 https://openlineage.io/spec/ 兼容)
    ol_run_event: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


# === Description (M2.x) ===
class DescriptionUpdate(BaseModel):
    description: str = Field(..., min_length=1, max_length=2048)
    source: Literal["manual", "ai_inferred", "imported"] = "manual"
    rationale: str | None = Field(None, max_length=2048)


class AssetDescriptionCoverage(BaseModel):
    """M2.x: 描述覆盖率统计."""

    tables_total: int
    tables_with_description: int
    tables_with_ai_description: int
    tables_with_manual_description: int
    columns_total: int
    columns_with_description: int
    columns_with_ai_description: int
    columns_with_manual_description: int
    table_lineage_total: int
    table_lineage_with_description: int
    column_lineage_total: int
    column_lineage_with_description: int
    table_coverage_pct: float
    column_coverage_pct: float
    lineage_coverage_pct: float


# === AISuggestion ===
class SuggestionApprove(BaseModel):
    review_note: str | None = Field(None, max_length=2048)


class SuggestionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    suggestion_type: str
    target_type: str
    target_id: UUID
    payload: dict[str, Any]
    rationale: str | None
    confidence: float
    model: str
    skill: str
    use_case_id: str | None
    status: str
    created_at: datetime
    reviewed_at: datetime | None
    review_note: str | None


class SuggestionListResponse(BaseModel):
    items: list[SuggestionRead]
    total: int


# === AssetOwner ===
class AssetOwnerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    table_id: UUID
    table_fqn: str | None = None
    user_email: str
    user_name: str | None
    team: str | None
    role: str
    source: str
    confidence: float
    is_verified: bool
    created_at: datetime
    updated_at: datetime


class AssetOwnerListResponse(BaseModel):
    items: list[AssetOwnerRead]
    total: int


# === Tag ===
class TagCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    category: Literal["pii", "tier", "domain", "status", "custom"] = "custom"
    color: str = Field("#697077", pattern=r"^#[0-9a-fA-F]{6}$")
    description: str | None = Field(None, max_length=512)


class TagUpdate(BaseModel):
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    description: str | None = Field(None, max_length=512)


class TagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    category: str
    color: str
    description: str | None
    asset_count: int = 0
    created_at: datetime
    updated_at: datetime


class TagListResponse(BaseModel):
    items: list[TagRead]
    total: int


class TagBindRequest(BaseModel):
    tag_id: UUID
    source: Literal["manual", "ai_inferred", "policy"] = "manual"


# === Glossary ===
class GlossaryTermCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    definition: str = Field(..., min_length=1, max_length=2048)
    domain: str | None = Field(None, max_length=64)
    owner_team: str | None = Field(None, max_length=128)
    synonyms: list[str] = Field(default_factory=list)


class GlossaryTermUpdate(BaseModel):
    definition: str | None = Field(None, max_length=2048)
    domain: str | None = Field(None, max_length=64)
    owner_team: str | None = Field(None, max_length=128)
    synonyms: list[str] | None = None


class GlossaryTermRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    definition: str
    domain: str | None
    owner_team: str | None
    synonyms: list[str]
    asset_count: int = 0
    created_at: datetime
    updated_at: datetime


class GlossaryListResponse(BaseModel):
    items: list[GlossaryTermRead]
    total: int


class GlossaryBindRequest(BaseModel):
    term_id: UUID
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    source: str = "manual"


# === UseCase ===
class UseCaseSummary(BaseModel):
    id: str
    version: int
    description: str
    owners: list[str]
    sources_count: int
    analysis_count: int
    path: str
    updated_at: datetime | None = None


class UseCaseRead(UseCaseSummary):
    raw: str  # raw YAML text
    spec: dict[str, Any]  # parsed spec


class UseCaseSave(BaseModel):
    """写 use case 时的输入 (YAML 文本优先, 便于 GUI 直接编辑)."""

    raw: str = Field(..., min_length=1)
    message: str | None = None


class UseCaseListResponse(BaseModel):
    items: list[UseCaseSummary]
    total: int


# === Use Case Trigger / Rescan ===
class UseCaseTriggerRequest(BaseModel):
    """触发 use case 编排 (全量或单阶段).

    use_case_id: 留空时由 router 从 path 注入
    stages: None / 空 = 走 use_case.sources 全量 (analyze_data_pipeline)
    stages: ['1','2',...] = 仅跑指定 stage 号 (按 stage filter)
    dry_run: True 时所有 skill 走 dry_run, 不写库
    """

    use_case_id: str | None = None
    stages: list[int] | None = None
    dry_run: bool = False
    apply: bool = True


class UseCaseStageRequest(BaseModel):
    """单阶段触发 (M3.5+ 用于按需 re-scan 某个阶段的资产/血缘)."""

    stage: int = Field(..., ge=1, le=6)
    dry_run: bool = False


class UseCaseTriggerResponse(BaseModel):
    ok: bool
    use_case_id: str
    stage: int | None = None  # None = 全量
    output: dict[str, Any]
    error: str | None = None
    duration_ms: int = 0


# === System-wide Re-scan (M3.5+ 资源级) ===
class RescanAssetRequest(BaseModel):
    """按 source_type 重扫一组资产."""

    source_type: Literal[
        "gcs", "clickhouse", "superset_export", "superset_db",
        "github", "dbt", "mex", "all",
    ] = "all"
    bucket: str | None = None  # 仅 gcs
    database: str | None = None  # 仅 clickhouse
    service_name: str | None = None
    dry_run: bool = False


class RescanAssetResponse(BaseModel):
    ok: bool
    source_type: str
    items_count: int
    by_subtype: dict[str, int] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: int = 0


# === Search ===
class SearchHit(BaseModel):
    kind: Literal["asset", "owner", "tag", "glossary", "use_case", "suggestion"]
    id: str
    title: str
    subtitle: str | None = None
    url: str
    score: float = 1.0
    extra: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    query: str
    total: int
    items: list[SearchHit]


# === ChatBI (M4) ===
class ChatBIRequest(BaseModel):
    """ChatBI v1 输入: 自然语言 -> SQL (5 层 Guard 在 nl2sql skill 里)."""

    question: str = Field(..., min_length=1, max_length=2000)
    service: str | None = Field(None, description="限定 service (空 = 全部)")
    dry_run: bool = Field(True, description="仅生成 SQL, 不真跑")


class ChatBIResponse(BaseModel):
    question: str
    sql: str | None
    rationale: str | None
    confidence: float
    guard_warnings: list[str] = Field(default_factory=list)
    result_sample: list[dict[str, Any]] = Field(default_factory=list)
    chart_hint: str | None = None
    duration_ms: int = 0
    model: str | None = None


# === Impact Analysis (M3) ===
class ImpactAnalysisResponse(BaseModel):
    """表影响分析: 上游/下游 + 受影响 owner/term."""

    center_fqn: str
    center_id: UUID
    direction: Literal["upstream", "downstream", "both"]
    depth: int
    upstream_count: int
    downstream_count: int
    affected_owners: list[str]
    affected_terms: list[str]
    paths: list[dict[str, Any]]  # [{from, to, via}, ...]


# === Data Quality (M4) ===
class QualityRuleCreate(BaseModel):
    table_id: UUID
    name: str = Field(..., min_length=1, max_length=128)
    rule_type: Literal["freshness", "volume", "null_ratio", "distinct", "custom", "anomaly"]
    severity: Literal["info", "warning", "critical"] = "warning"
    definition: dict[str, Any] = Field(default_factory=dict)
    schedule: str = "0 * * * *"
    description: str | None = None


class QualityRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    table_id: UUID
    name: str
    rule_type: str
    severity: str
    definition: dict[str, Any]
    schedule: str
    is_enabled: bool
    description: str | None
    created_at: datetime
    updated_at: datetime


class QualityRuleListResponse(BaseModel):
    items: list[QualityRuleRead]
    total: int


class QualityDashboard(BaseModel):
    avg_health_score: float | None
    tables_total: int
    tables_low: int  # health < 70
    tables_critical: int  # health < 40
    rules_total: int
    rules_failing: int
    recent_anomalies: list[dict[str, Any]]  # ai_suggestion items
    last_run: datetime | None
