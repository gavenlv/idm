"""M1 初始 schema: services / databases / schemas / table_assets / column_assets /

table_lineage / tags / asset_tags / asset_owners / glossary_terms / asset_terms /
quality_rules / quality_results / ai_suggestions / audit_logs

设计见 docs/design/data-model.md 与 docs/AGENT_INSTRUCTIONS.md §7。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # === 1) 扩展 ===
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # AGE 由 init-postgres.sh 尝试安装; 若不可用则忽略 (降级到 PG-only 图查询)

    # === 2) services ===
    op.create_table(
        "services",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("description", sa.String(1024), nullable=True),
        sa.Column("config", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("tier", sa.String(16), nullable=False, server_default="normal"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_services_name", "services", ["name"])

    # === 3) databases ===
    op.create_table(
        "databases",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("services.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("service_id", "name", name="uq_databases_service_name"),
    )
    op.create_index("ix_databases_service_id", "databases", ["service_id"])

    # === 4) schemas ===
    op.create_table(
        "schemas",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("database_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("databases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("database_id", "name", name="uq_schemas_database_name"),
    )
    op.create_index("ix_schemas_database_id", "schemas", ["database_id"])

    # === 5) table_assets ===
    op.create_table(
        "table_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("schema_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("schemas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("fqn", sa.String(512), nullable=False),
        sa.Column("asset_type", sa.String(32), nullable=False, server_default="table"),
        sa.Column("tier", sa.String(16), nullable=False, server_default="normal"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("description", sa.String(4096), nullable=True),
        sa.Column("description_source", sa.String(32), nullable=True),
        sa.Column("last_profiled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("column_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("row_count", sa.BigInteger, nullable=True),
        sa.Column("size_bytes", sa.BigInteger, nullable=True),
        sa.Column("last_query_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("query_count_30d", sa.Integer, nullable=False, server_default="0"),
        sa.Column("extra", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("schema_id", "name", name="uq_table_assets_schema_name"),
        sa.UniqueConstraint("fqn", name="uq_table_assets_fqn"),
    )
    op.create_index("ix_table_assets_schema_id", "table_assets", ["schema_id"])
    op.create_index("ix_table_assets_fqn", "table_assets", ["fqn"])
    op.create_index("ix_table_assets_name", "table_assets", ["name"])
    op.create_index("ix_table_assets_tier", "table_assets", ["tier"])
    op.create_index("ix_table_assets_status", "table_assets", ["status"])

    # === 6) column_assets ===
    op.create_table(
        "column_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("table_assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("data_type", sa.String(64), nullable=False),
        sa.Column("nullable", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("is_primary_key", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("is_partition_key", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("description", sa.String(2048), nullable=True),
        sa.Column("pii_class", sa.String(32), nullable=False, server_default="none"),
        sa.Column("pii_confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("pii_source", sa.String(32), nullable=True),
        sa.Column("sample_values", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("null_ratio", sa.Float, nullable=False, server_default="0"),
        sa.Column("distinct_count", sa.Integer, nullable=True),
        sa.Column("extra", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("table_id", "name", name="uq_column_assets_table_name"),
    )
    op.create_index("ix_column_assets_table_id", "column_assets", ["table_id"])
    op.create_index("ix_column_assets_pii_class", "column_assets", ["pii_class"])

    # === 7) table_lineage ===
    op.create_table(
        "table_lineage",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("upstream_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("table_assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("downstream_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("table_assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("transform_type", sa.String(32), nullable=False, server_default="copy"),
        sa.Column("job_id", sa.String(256), nullable=True),
        sa.Column("sql", sa.String(8192), nullable=True),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("source", sa.String(32), nullable=False, server_default="ai_inferred"),
        sa.Column("extra", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("upstream_id", "downstream_id", "transform_type", name="uq_lineage_up_down_type"),
    )
    op.create_index("ix_table_lineage_upstream_id", "table_lineage", ["upstream_id"])
    op.create_index("ix_table_lineage_downstream_id", "table_lineage", ["downstream_id"])

    # === 8) tags / asset_tags ===
    op.create_table(
        "tags",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("category", sa.String(32), nullable=False, server_default="custom"),
        sa.Column("color", sa.String(16), nullable=False, server_default="#999999"),
        sa.Column("description", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("name", name="uq_tags_name"),
    )
    op.create_index("ix_tags_name", "tags", ["name"])

    op.create_table(
        "asset_tags",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("table_assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tag_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tags.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("table_id", "tag_id", name="uq_asset_tags_table_tag"),
    )
    op.create_index("ix_asset_tags_table_id", "asset_tags", ["table_id"])
    op.create_index("ix_asset_tags_tag_id", "asset_tags", ["tag_id"])

    # === 9) asset_owners ===
    op.create_table(
        "asset_owners",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("table_assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_email", sa.String(256), nullable=False),
        sa.Column("user_name", sa.String(256), nullable=True),
        sa.Column("team", sa.String(128), nullable=True),
        sa.Column("role", sa.String(32), nullable=False, server_default="owner"),
        sa.Column("source", sa.String(32), nullable=False, server_default="ai_inferred"),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("is_verified", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("table_id", "user_email", "role", name="uq_owners_table_user_role"),
    )
    op.create_index("ix_asset_owners_table_id", "asset_owners", ["table_id"])
    op.create_index("ix_asset_owners_user_email", "asset_owners", ["user_email"])
    op.create_index("ix_asset_owners_team", "asset_owners", ["team"])

    # === 10) glossary_terms / asset_terms ===
    op.create_table(
        "glossary_terms",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("definition", sa.String(2048), nullable=False),
        sa.Column("domain", sa.String(64), nullable=True),
        sa.Column("owner_team", sa.String(128), nullable=True),
        sa.Column("synonyms", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("extra", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("name", name="uq_glossary_terms_name"),
    )
    op.create_index("ix_glossary_terms_name", "glossary_terms", ["name"])
    op.create_index("ix_glossary_terms_domain", "glossary_terms", ["domain"])

    op.create_table(
        "asset_terms",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("table_assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("term_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("glossary_terms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("source", sa.String(32), nullable=False, server_default="ai_inferred"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("table_id", "term_id", name="uq_asset_terms_table_term"),
    )
    op.create_index("ix_asset_terms_table_id", "asset_terms", ["table_id"])
    op.create_index("ix_asset_terms_term_id", "asset_terms", ["term_id"])

    # === 11) quality_rules / quality_results ===
    op.create_table(
        "quality_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("table_assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("rule_type", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="warning"),
        sa.Column("definition", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("schedule", sa.String(64), nullable=False, server_default="0 * * * *"),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("table_id", "name", name="uq_quality_rules_table_name"),
    )
    op.create_index("ix_quality_rules_table_id", "quality_rules", ["table_id"])

    op.create_table(
        "quality_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("quality_rules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("passed", sa.Boolean, nullable=False),
        sa.Column("observed_value", sa.Float, nullable=True),
        sa.Column("threshold", sa.Float, nullable=True),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("extra", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("duration_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_quality_results_rule_id", "quality_results", ["rule_id"])
    op.create_index("ix_quality_results_created_at", "quality_results", ["created_at"])

    # === 12) ai_suggestions ===
    op.create_table(
        "ai_suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("suggestion_type", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("rationale", sa.Text, nullable=True),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0.5"),
        sa.Column("model", sa.String(64), nullable=False, server_default="gpt-5"),
        sa.Column("skill", sa.String(128), nullable=False),
        sa.Column("use_case_id", sa.String(128), nullable=True),
        sa.Column("prompt_hash", sa.String(64), nullable=True),
        sa.Column("langfuse_trace_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("reviewed_by", sa.String(256), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_ai_suggestions_suggestion_type", "ai_suggestions", ["suggestion_type"])
    op.create_index("ix_ai_suggestions_target_type", "ai_suggestions", ["target_type"])
    op.create_index("ix_ai_suggestions_target_id", "ai_suggestions", ["target_id"])
    op.create_index("ix_ai_suggestions_status", "ai_suggestions", ["status"])
    op.create_index("ix_ai_suggestions_skill", "ai_suggestions", ["skill"])
    op.create_index("ix_ai_suggestions_use_case_id", "ai_suggestions", ["use_case_id"])
    op.create_index("ix_ai_suggestions_langfuse_trace_id", "ai_suggestions", ["langfuse_trace_id"])

    # === 13) audit_logs ===
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("actor", sa.String(256), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=True),
        sa.Column("resource_id", sa.String(64), nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("ip_address", postgresql.INET, nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("result", sa.String(16), nullable=False, server_default="success"),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_audit_logs_actor", "audit_logs", ["actor"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_resource_type", "audit_logs", ["resource_type"])
    op.create_index("ix_audit_logs_resource_id", "audit_logs", ["resource_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    # 反向删除
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_ai_suggestions_langfuse_trace_id", table_name="ai_suggestions")
    op.drop_index("ix_ai_suggestions_use_case_id", table_name="ai_suggestions")
    op.drop_index("ix_ai_suggestions_skill", table_name="ai_suggestions")
    op.drop_index("ix_ai_suggestions_status", table_name="ai_suggestions")
    op.drop_index("ix_ai_suggestions_target_id", table_name="ai_suggestions")
    op.drop_index("ix_ai_suggestions_target_type", table_name="ai_suggestions")
    op.drop_index("ix_ai_suggestions_suggestion_type", table_name="ai_suggestions")
    op.drop_table("ai_suggestions")

    op.drop_index("ix_quality_results_created_at", table_name="quality_results")
    op.drop_index("ix_quality_results_rule_id", table_name="quality_results")
    op.drop_table("quality_results")
    op.drop_index("ix_quality_rules_table_id", table_name="quality_rules")
    op.drop_table("quality_rules")

    op.drop_index("ix_asset_terms_term_id", table_name="asset_terms")
    op.drop_index("ix_asset_terms_table_id", table_name="asset_terms")
    op.drop_table("asset_terms")
    op.drop_index("ix_glossary_terms_domain", table_name="glossary_terms")
    op.drop_index("ix_glossary_terms_name", table_name="glossary_terms")
    op.drop_table("glossary_terms")

    op.drop_index("ix_asset_owners_team", table_name="asset_owners")
    op.drop_index("ix_asset_owners_user_email", table_name="asset_owners")
    op.drop_index("ix_asset_owners_table_id", table_name="asset_owners")
    op.drop_table("asset_owners")

    op.drop_index("ix_asset_tags_tag_id", table_name="asset_tags")
    op.drop_index("ix_asset_tags_table_id", table_name="asset_tags")
    op.drop_table("asset_tags")
    op.drop_index("ix_tags_name", table_name="tags")
    op.drop_table("tags")

    op.drop_index("ix_table_lineage_downstream_id", table_name="table_lineage")
    op.drop_index("ix_table_lineage_upstream_id", table_name="table_lineage")
    op.drop_table("table_lineage")

    op.drop_index("ix_column_assets_pii_class", table_name="column_assets")
    op.drop_index("ix_column_assets_table_id", table_name="column_assets")
    op.drop_table("column_assets")

    op.drop_index("ix_table_assets_status", table_name="table_assets")
    op.drop_index("ix_table_assets_tier", table_name="table_assets")
    op.drop_index("ix_table_assets_name", table_name="table_assets")
    op.drop_index("ix_table_assets_fqn", table_name="table_assets")
    op.drop_index("ix_table_assets_schema_id", table_name="table_assets")
    op.drop_table("table_assets")

    op.drop_index("ix_schemas_database_id", table_name="schemas")
    op.drop_table("schemas")

    op.drop_index("ix_databases_service_id", table_name="databases")
    op.drop_table("databases")

    op.drop_index("ix_services_name", table_name="services")
    op.drop_table("services")
