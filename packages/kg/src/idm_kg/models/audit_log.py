"""AuditLog: 全量审计 (LLM 调用 / MCP 调用 / 资产变更 / 权限)."""
from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from idm_kg.models.base import Base, TimestampMixin, UUIDMixin


class AuditLog(Base, UUIDMixin, TimestampMixin):
    """全量审计日志 (append-only)。

    记录: LLM 调用 / MCP 调用 / 资产 CRUD / 权限决策 / 建议审核。
    """

    __tablename__ = "audit_logs"

    actor: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    # user@example.com 或 system:planner 或 system:agent:doc
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # create_table / approve_suggestion / llm_call / mcp_call / ...
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    result: Mapped[str] = mapped_column(String(16), default="success", nullable=False)
    # success / failure / denied
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
