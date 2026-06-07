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
    fqn: str = Field(..., max_length=512, pattern=r"^[a-z0-9_.:-]+$")
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


class LineageGraphResponse(BaseModel):
    """以一张表为中心的 lineage 视图 (depth=BFS 上/下游 N 层)."""

    center_fqn: str
    center_id: UUID
    upstream: list[LineageEdgeRead]  # 边列表, 端点 fqn 已展开
    downstream: list[LineageEdgeRead]
    nodes: list[dict[str, Any]]  # [{id, fqn, asset_type, tier}, ...] 去重
    edges: list[LineageEdgeRead]


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
