"""Eval Harness 数据模型 (Pydantic).

EvalCase:        1 个测试用例 (input + gold + rubric)
EvalResult:      1 个用例的跑分结果
EvalReport:      1 个 skill 的整体报告
GateConfig:      门禁阈值
GateResult:      门禁判定
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# === Rubric V1: LLM judge 的评分细则 ===
class RubricV1(BaseModel):
    """LLM-as-judge 用的 rubric. 维度 + 权重 + judge model."""

    type: Literal["rubric_v1"] = "rubric_v1"
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "accuracy": 0.4,
            "specificity": 0.3,
            "length": 0.1,
            "fluency": 0.2,
        }
    )
    judge_model: str = "gpt-5"
    notes: str | None = None


# === EvalCase: 一个测试用例 ===
class EvalCase(BaseModel):
    """一个测试用例: 1 个 input + 期望 gold + 评分规则."""

    id: str
    skill: str
    input: dict[str, Any]
    gold: dict[str, Any] = Field(
        default_factory=dict,
        description="期望输出. exact / contains / must_contain / must_not_contain 等",
    )
    rubric: RubricV1 | dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list)

    def expected_kind(self) -> str:
        """gold 字段的 kind: exact / contains / list_contains / fqn_match / sql_safety / 等."""
        if not self.gold:
            return "freeform"
        if "must_contain" in self.gold or "must_not_contain" in self.gold:
            return "list_contains"
        if "contains" in self.gold or "max_length" in self.gold or "min_length" in self.gold:
            return "contains"
        if "exact" in self.gold:
            return "exact"
        if "fqn" in self.gold:
            return "fqn_match"
        if "sql_safety" in self.gold or "guards" in self.gold:
            return "guard_match"
        return "freeform"


# === EvalResult: 单 case 跑分结果 ===
class EvalResult(BaseModel):
    case_id: str
    skill: str
    pred: dict[str, Any] = Field(default_factory=dict)
    score: float = 0.0
    sub_scores: dict[str, float] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    rationale: str | None = None
    latency_ms: int = 0
    tokens: int = 0
    cost: float = 0.0
    judge_model: str | None = None
    error: str | None = None


# === EvalReport: 1 个 skill 的整体报告 ===
class EvalReport(BaseModel):
    skill: str
    model: str = "n/a"
    judge_model: str = "gpt-5"
    total: int = 0
    passed: int = 0
    avg_score: float = 0.0
    p50_latency: float = 0.0
    total_cost: float = 0.0
    per_case: list[EvalResult] = Field(default_factory=list)
    markdown: str = ""
    created_at: str = ""


# === Gate: PR 阻塞阈值 ===
class GateConfig(BaseModel):
    min_avg_score: float = 0.7
    min_pass_rate: float = 0.7
    max_regress: float = 0.05  # 平均分退化超过 5% 即 fail
    max_cost_per_case_usd: float = 0.5


class GateResult(BaseModel):
    passed: bool
    reason: str
    report_avg: float
    baseline_avg: float | None = None
    report_pass_rate: float
    details: dict[str, Any] = Field(default_factory=dict)
