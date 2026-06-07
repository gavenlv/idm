"""AISuggestion: LLM 建议, 必须经人工确认才能写入正式字段。

AGENT_INSTRUCTIONS §1 原则 5: AI in the Loop, Human in the Lead。
任何 LLM 写元数据 / 自动派 Insight / 改生产表 → 先入本表 pending → 人工 approve/reject。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from idm_kg.models.base import Base, TimestampMixin, UUIDMixin


class AISuggestion(Base, UUIDMixin, TimestampMixin):
    """LLM 建议 (description / pii_class / owner / lineage / insight / quality_rule ...)."""

    __tablename__ = "ai_suggestions"

    # 建议类型
    suggestion_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # description / pii_class / owner / lineage / glossary / quality_rule / insight
    target_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # table / column / glossary / rule
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    # FK 不加, 避免多类型混表跨约束

    # 建议内容
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 信任度 / 模型
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    model: Mapped[str] = mapped_column(String(64), default="gpt-5", nullable=False)
    skill: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    use_case_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # 状态
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    # pending / approved / rejected / auto_approved / expired
    reviewed_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
