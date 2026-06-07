"""LLM-as-judge: 用 LLM 对 Skill 输出打分 (0~1) + 原因.

约束:
- 输出严格 JSON: {score, sub_scores, issues, rationale}
- judge 自身也需校准 (与人类标注 Pearson >= 0.85)
- 默认 judge model = gpt-5 (可被 case.rubric.judge_model 覆盖)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from idm_api.eval.types import EvalCase, EvalResult, RubricV1
from idm_api.skills.llm import LLMUnavailableError, get_llm_router

logger = logging.getLogger(__name__)


# === Judge 提示词模板 ===
JUDGE_PROMPT = """你是严格的数据治理审核员, 负责评估 LLM 生成结果的质量。
请按以下维度 (0~1 浮点分) 逐项打分, 最后输出加权总分 + 0~3 条最关键 issues。

【评估维度 (weights)】
{weights_text}

【用户输入 (input)】
{input_text}

【预测输出 (pred)】
{pred_text}

【期望输出 (gold)】
{gold_text}

【特别说明 (notes)】
{notes}

要求:
- score ∈ [0, 1] 浮点
- sub_scores 的 key 必须是上面 weights 里的维度
- issues 用中文短句, 0~3 条, 写"什么不对 + 怎么修"
- rationale 用 1-3 句话总结

输出严格 JSON (无 markdown 围栏, 无解释文字):
{{
  "score": <float>,
  "sub_scores": {{ "<dim1>": <float>, ... }},
  "issues": ["...", "..."],
  "rationale": "..."
}}
"""


def _format_weights(weights: dict[str, float]) -> str:
    return "\n".join(f"- {k}: 权重 {v:.2f}" for k, v in weights.items())


def _truncate(text: str, max_len: int = 2000) -> str:
    """截断过长的文本, 避免 prompt 爆."""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 50] + f"\n... (truncated, total {len(text)} chars)"


class JudgeOutput(BaseModel):
    score: float
    sub_scores: dict[str, float] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    rationale: str | None = None


class LLMJudge:
    """LLM-as-judge. 依赖 LLMRouter (有 key 时用 LLM, 无 key 时抛 LLMUnavailableError)."""

    def __init__(self, model: str | None = None):
        self._default_model = model

    async def score(
        self,
        case: EvalCase,
        pred: dict[str, Any],
    ) -> tuple[JudgeOutput, str, int, int]:
        """返回 (judge_output, judge_model, tokens, latency_ms)."""
        rubric = case.rubric if isinstance(case.rubric, RubricV1) else None
        if rubric is None and isinstance(case.rubric, dict):
            rubric = RubricV1(**case.rubric)
        if rubric is None:
            rubric = RubricV1()

        judge_model = self._default_model or rubric.judge_model
        weights = rubric.weights

        prompt = JUDGE_PROMPT.format(
            weights_text=_format_weights(weights),
            input_text=_truncate(json.dumps(case.input, ensure_ascii=False, indent=2)),
            pred_text=_truncate(json.dumps(pred, ensure_ascii=False, indent=2)),
            gold_text=_truncate(json.dumps(case.gold, ensure_ascii=False, indent=2)),
            notes=rubric.notes or "(无)",
        )

        router = get_llm_router()
        import time as _time

        t0 = _time.perf_counter()
        try:
            resp = await router.complete(
                [
                    {
                        "role": "system",
                        "content": "你是严格的数据治理审核员, 输出严格 JSON。",
                    },
                    {"role": "user", "content": prompt},
                ],
                profile="default",
                temperature=0.0,
                max_tokens=400,
                response_format={"type": "json_object"},
            )
        except LLMUnavailableError as e:
            logger.warning("LLM judge unavailable, falling back to rule-based: %s", e)
            return self._fallback_rule_based(case, pred), judge_model, 0, int((_time.perf_counter() - t0) * 1000)

        latency_ms = int((_time.perf_counter() - t0) * 1000)
        tokens = int((resp.get("usage") or {}).get("total_tokens") or 0)
        actual_model = resp.get("model", judge_model)
        text = resp.get("content", "").strip()
        # 去掉 markdown 围栏
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(l for l in lines if not l.strip().startswith("```"))
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            logger.warning("judge returned non-JSON: %s", text[:200])
            return (
                JudgeOutput(score=0.0, issues=[f"judge non-JSON: {text[:80]}"]),
                actual_model,
                tokens,
                latency_ms,
            )
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            return (
                JudgeOutput(score=0.0, issues=[f"judge JSON parse: {e}"]),
                actual_model,
                tokens,
                latency_ms,
            )

        # 兼容带引号的 key
        if "score" not in data:
            for k in list(data.keys()):
                if k.strip('"\'') == "score":
                    data["score"] = data.pop(k)
                    break

        score = float(data.get("score", 0.0))
        score = max(0.0, min(1.0, score))
        sub = data.get("sub_scores") or {}
        sub = {str(k).strip('"\''): float(v) for k, v in sub.items() if v is not None}
        issues = data.get("issues") or []
        if not isinstance(issues, list):
            issues = [str(issues)]
        rationale = data.get("rationale")

        return (
            JudgeOutput(score=score, sub_scores=sub, issues=issues, rationale=rationale),
            actual_model,
            tokens,
            latency_ms,
        )

    def _fallback_rule_based(
        self, case: EvalCase, pred: dict[str, Any]
    ) -> JudgeOutput:
        """无 LLM key 时的兜底: 纯规则打分. 用于 CI 烟雾测试."""
        kind = case.expected_kind()
        issues: list[str] = []
        score = 0.5  # baseline

        if kind == "exact":
            ok = case.gold.get("exact") == pred
            score = 1.0 if ok else 0.0
            if not ok:
                issues.append(f"exact mismatch: expected={case.gold.get('exact')}, got={pred}")
        elif kind == "contains":
            blob = json.dumps(pred, ensure_ascii=False).lower()
            contains = [str(x).lower() for x in case.gold.get("contains", [])]
            hits = sum(1 for c in contains if c in blob)
            score = hits / max(1, len(contains))
            if score < 1.0:
                missing = [c for c in contains if c not in blob]
                issues.append(f"missing keywords: {missing}")
            max_len = case.gold.get("max_length")
            if max_len and blob and len(blob) > max_len * 2:
                # 粗略: 文本超过 max_length 1.5 倍扣分
                issues.append(f"可能超过 max_length={max_len}")
                score *= 0.8
        elif kind == "list_contains":
            sql = (pred.get("sql") or "").lower()
            must = [str(x).lower() for x in case.gold.get("must_contain", [])]
            forbid = [str(x).lower() for x in case.gold.get("must_not_contain", [])]
            hits = sum(1 for m in must if m in sql)
            misses = [m for m in must if m not in sql]
            fohits = sum(1 for m in forbid if m in sql)
            score = 0.5 + 0.5 * (hits / max(1, len(must))) - 0.5 * fohits
            if misses:
                issues.append(f"missing: {misses}")
            if fohits:
                issues.append(f"forbidden keyword hit: {[m for m in forbid if m in sql]}")
        elif kind == "guard_match":
            guards_required = set(case.gold.get("guards", []))
            guards_passed = set((pred.get("validation") or {}).get("passed_guards", []))
            miss = guards_required - guards_passed
            score = (len(guards_required) - len(miss)) / max(1, len(guards_required))
            if miss:
                issues.append(f"guards not passed: {sorted(miss)}")
        else:
            # freeform: 模糊分
            score = 0.6
            issues.append("freeform gold; 规则打分 0.6 (建议配 judge)")

        score = max(0.0, min(1.0, score))
        return JudgeOutput(score=score, issues=issues, rationale="rule-based fallback")
