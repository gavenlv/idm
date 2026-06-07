"""idm-kg: IDM Knowledge Graph domain models.

三层架构 (CloudSQL PostgreSQL 单实例多扩展):
- 关系层 (SQLAlchemy ORM)
- 图查询层 (Apache AGE - 异步同步)
- 向量层 (pgvector)

详见 docs/design/data-model.md 与 docs/AGENT_INSTRUCTIONS.md §7。
"""
from idm_kg.models.base import Base, TimestampMixin, UUIDMixin
from idm_kg.models.service import Service
from idm_kg.models.database import Database
from idm_kg.models.schema import Schema
from idm_kg.models.table_asset import TableAsset
from idm_kg.models.column_asset import ColumnAsset
from idm_kg.models.table_lineage import TableLineage
from idm_kg.models.tag import Tag, AssetTag
from idm_kg.models.owner import AssetOwner
from idm_kg.models.glossary import GlossaryTerm, AssetTerm
from idm_kg.models.quality import QualityRule, QualityResult
from idm_kg.models.ai_suggestion import AISuggestion
from idm_kg.models.audit_log import AuditLog

__all__ = [
    "Base",
    "TimestampMixin",
    "UUIDMixin",
    "Service",
    "Database",
    "Schema",
    "TableAsset",
    "ColumnAsset",
    "TableLineage",
    "Tag",
    "AssetTag",
    "AssetOwner",
    "GlossaryTerm",
    "AssetTerm",
    "QualityRule",
    "QualityResult",
    "AISuggestion",
    "AuditLog",
]
