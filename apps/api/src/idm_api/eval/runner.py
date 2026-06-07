"""SkillEvalRunner: 批量跑 Gold Case -> 打分 -> 报告.

设计:
- run_skill_case: 跑 1 个 case (ctx + inputs) -> pred
- score_case: 用 judge 打分 -> EvalResult
- run: 跑所有 case -> EvalReport
- save: 持久化 (json + markdown)
- gate: 对比 baseline -> 决定 pass/fail
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Iterable

from idm_api.eval.judge import LLMJudge
from idm_api.eval.types import (
    EvalCase,
    EvalReport,
    EvalResult,
    GateConfig,
    GateResult,
)
from idm_api.skills.mcp import get_clickhouse_mcp
from idm_api.skills.registry import (
    SkillContext,
    SkillOutput,
    get_registry,
)
from idm_api.skills.llm import get_llm_router

logger = logging.getLogger(__name__)


class SkillEvalRunner:
    """对 1 个 skill 跑全量 gold cases, 产出 report."""

    def __init__(
        self,
        skill: str,
        *,
        model: str | None = None,
        judge_model: str | None = None,
        parallel: int = 3,
    ) -> None:
        self.skill = skill
        self.model = model
        self.judge_model = judge_model
        self.parallel = max(1, parallel)
        self._judge = LLMJudge(model=judge_model)

    def _resolve_skill(self) -> tuple[int, str, Any]:
        # 关键: 先 import builtin skills, 否则 registry 是空的
        from idm_api.eval.cli import _load_builtin_skills  # 避免循环 import

        _load_builtin_skills()
        version, agent, handler = get_registry().get(self.skill)
        return version, agent, handler

    async def _run_case(self, case: EvalCase) -> EvalResult:
        """跑 1 个 case: 调用 skill -> judge -> EvalResult."""
        version, agent, handler = self._resolve_skill()
        # 构造 ctx (不真写 KG, dry_run 强制)
        ctx = SkillContext(
            db=None,
            llm=get_llm_router(),
            mcp={"clickhouse": get_clickhouse_mcp()},
            use_case_id=f"eval:{case.id}",
            dry_run=True,
        )
        ctx.log("eval_case_start", case_id=case.id, skill=self.skill)

        t0 = time.perf_counter()
        try:
            sr = await handler(ctx, **case.input)
        except Exception as e:  # noqa: BLE001
            logger.warning("skill %s failed on case %s: %s", self.skill, case.id, e)
            return EvalResult(
                case_id=case.id,
                skill=self.skill,
                pred={"error": str(e)},
                score=0.0,
                issues=[f"skill exception: {type(e).__name__}: {str(e)[:200]}"],
                latency_ms=int((time.perf_counter() - t0) * 1000),
                error=str(e),
            )

        # pred 序列化: 把 SkillOutput / SkillResult 转成 dict
        if hasattr(sr, "model_dump"):
            pred = sr.model_dump(mode="json")
        elif isinstance(sr, dict):
            pred = sr
        else:
            ok = getattr(sr, "ok", None)
            out = getattr(sr, "output", None)
            # 递归把 SkillOutput 转 dict
            if hasattr(out, "model_dump"):
                out = out.model_dump(mode="json")
            pred = {"ok": ok, "output": out}

        latency_ms = int((time.perf_counter() - t0) * 1000)

        # judge
        try:
            judge_out, used_judge, tokens, judge_ms = await self._judge.score(case, pred)
        except Exception as e:  # noqa: BLE001
            logger.warning("judge failed on case %s: %s", case.id, e)
            return EvalResult(
                case_id=case.id,
                skill=self.skill,
                pred=pred,
                score=0.0,
                issues=[f"judge exception: {e}"],
                latency_ms=latency_ms,
                error=str(e),
            )

        return EvalResult(
            case_id=case.id,
            skill=self.skill,
            pred=pred,
            score=judge_out.score,
            sub_scores=judge_out.sub_scores,
            issues=judge_out.issues,
            rationale=judge_out.rationale,
            latency_ms=latency_ms + judge_ms,
            tokens=tokens,
            judge_model=used_judge,
        )

    async def run(self, cases: list[EvalCase]) -> EvalReport:
        """并发跑所有 cases, 汇总成 report."""
        if not cases:
            return EvalReport(skill=self.skill, model=self.model or "n/a")

        sem = asyncio.Semaphore(self.parallel)
        results: list[EvalResult] = []

        async def _one(c: EvalCase) -> EvalResult:
            async with sem:
                return await self._run_case(c)

        started = time.perf_counter()
        results = await asyncio.gather(*[_one(c) for c in cases])
        total_ms = int((time.perf_counter() - started) * 1000)

        # 统计
        scores = [r.score for r in results]
        avg = sum(scores) / max(1, len(scores))
        passed = sum(1 for s in scores if s >= 0.7)
        latencies = sorted([r.latency_ms for r in results])
        p50 = latencies[len(latencies) // 2] if latencies else 0
        total_cost = 0.0  # TODO: 接入 litellm cost tracker

        report = EvalReport(
            skill=self.skill,
            model=self.model or "n/a",
            judge_model=self.judge_model or "gpt-5",
            total=len(results),
            passed=passed,
            avg_score=round(avg, 4),
            p50_latency=p50,
            total_cost=round(total_cost, 4),
            per_case=results,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        report.markdown = self._render_markdown(report, total_ms)
        return report

    def _render_markdown(self, r: EvalReport, total_ms: int) -> str:
        lines: list[str] = []
        lines.append(f"# Skill Eval — {r.skill}")
        lines.append("")
        lines.append(f"- Date: {r.created_at}")
        lines.append(f"- Model: {r.model}")
        lines.append(f"- Judge: {r.judge_model}")
        lines.append(f"- Cases: {r.total}")
        lines.append(f"- Total wall time: {total_ms} ms")
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        lines.append(f"- Avg score: **{r.avg_score}**")
        lines.append(f"- Pass rate (>= 0.7): {r.passed}/{r.total} ({r.passed / max(1, r.total):.0%})")
        lines.append(f"- P50 latency: {r.p50_latency} ms")
        lines.append(f"- Total cost: ${r.total_cost}")
        lines.append("")
        # Failures
        bad = [c for c in r.per_case if c.score < 0.7]
        if bad:
            lines.append("## Failures (score < 0.7)")
            lines.append("")
            for c in bad:
                lines.append(f"### {c.case_id}  score={c.score:.2f}")
                if c.issues:
                    for iss in c.issues[:3]:
                        lines.append(f"- {iss}")
                if c.rationale:
                    lines.append(f"> {c.rationale}")
                lines.append("")
        # Top issues
        issue_count: dict[str, int] = {}
        for c in r.per_case:
            for iss in c.issues:
                # 简化: 截短 issue
                key = iss.split(":")[0].strip()[:60]
                issue_count[key] = issue_count.get(key, 0) + 1
        if issue_count:
            lines.append("## Top issue patterns")
            lines.append("")
            for k, v in sorted(issue_count.items(), key=lambda x: -x[1])[:5]:
                lines.append(f"- {k}  ({v} cases)")
            lines.append("")
        return "\n".join(lines)

    # === 持久化 ===
    def save(self, report: EvalReport, out_dir: str | Path) -> tuple[Path, Path]:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        json_path = out / f"{self.skill}.result.json"
        md_path = out / f"{self.skill}.report.md"
        json_path.write_text(
            json.dumps(
                report.model_dump(mode="json"), ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        md_path.write_text(report.markdown, encoding="utf-8")
        return json_path, md_path


# === 加载 Gold Cases ===
def load_cases(path: str | Path) -> list[EvalCase]:
    """从 .jsonl 文件加载 EvalCase. 每行 1 个 case."""
    p = Path(path)
    cases: list[EvalCase] = []
    for ln, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid json at {p}:{ln}: {e}") from e
        cases.append(EvalCase.model_validate(obj))
    return cases


# === Gate: PR 阻塞 ===
def gate(
    report: EvalReport,
    baseline: EvalReport | None,
    cfg: GateConfig | None = None,
) -> GateResult:
    """对比 baseline + 检查阈值. 用于 CI 阻塞 PR."""
    cfg = cfg or GateConfig()
    details: dict[str, Any] = {
        "min_avg_score": cfg.min_avg_score,
        "min_pass_rate": cfg.min_pass_rate,
        "max_regress": cfg.max_regress,
    }
    pass_rate = report.passed / max(1, report.total)

    reasons: list[str] = []
    if report.avg_score < cfg.min_avg_score:
        reasons.append(
            f"avg_score {report.avg_score:.3f} < min_avg_score {cfg.min_avg_score:.3f}"
        )
    if pass_rate < cfg.min_pass_rate:
        reasons.append(
            f"pass_rate {pass_rate:.2%} < min_pass_rate {cfg.min_pass_rate:.2%}"
        )
    if baseline is not None:
        delta = report.avg_score - baseline.avg_score
        details["delta"] = round(delta, 4)
        if delta < -cfg.max_regress:
            reasons.append(
                f"regress {delta:+.3f} < -{cfg.max_regress:.3f} (vs baseline {baseline.avg_score:.3f})"
            )

    return GateResult(
        passed=not reasons,
        reason="; ".join(reasons) if reasons else "OK",
        report_avg=report.avg_score,
        baseline_avg=baseline.avg_score if baseline else None,
        report_pass_rate=pass_rate,
        details=details,
    )
