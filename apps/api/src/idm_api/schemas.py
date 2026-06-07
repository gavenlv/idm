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
    fqn: str = Field(..., max_length=512, pattern=r"^[a-z0-9_.]+$")
    asset_type: Literal["table", "view", "materialized_view", "dbt_model", "dashboard"] = "table"
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
