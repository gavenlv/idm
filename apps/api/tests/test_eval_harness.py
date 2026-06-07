"""Eval Harness 单元测试: 验证核心逻辑, 无需 LLM key.

覆盖:
- EvalCase 解析 (expected_kind 推断)
- judge._fallback_rule_based 5 种 gold 类型的打分
- gate() 阈值判定
- load_cases 解析 .jsonl
- SkillEvalRunner 用 fake LLM/MCP 跑通 1 个 case
- CLI 入口可被 argparse 解析
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from idm_api.eval.judge import LLMJudge
from idm_api.eval.runner import SkillEvalRunner, gate, load_cases
from idm_api.eval.types import EvalCase, EvalReport, EvalResult, GateConfig, RubricV1
from idm_api.eval.cli import build_parser


# === 1) EvalCase 解析 ===
def test_evalcase_expected_kind():
    assert EvalCase(id="x", skill="s", input={}, gold={"exact": "a"}).expected_kind() == "exact"
    assert EvalCase(id="x", skill="s", input={}, gold={"contains": ["a"]}).expected_kind() == "contains"
    assert EvalCase(id="x", skill="s", input={}, gold={"must_contain": ["a"]}).expected_kind() == "list_contains"
    assert EvalCase(id="x", skill="s", input={}, gold={"fqn": "x"}).expected_kind() == "fqn_match"
    assert EvalCase(id="x", skill="s", input={}, gold={"guards": ["schema"]}).expected_kind() == "guard_match"
    assert EvalCase(id="x", skill="s", input={}).expected_kind() == "freeform"


def test_rubric_v1_defaults():
    r = RubricV1()
    assert r.weights["accuracy"] == 0.4
    assert r.judge_model == "gpt-5"
    assert r.type == "rubric_v1"


# === 2) Judge 兜底: 5 种 gold 类型 ===
def _fallback_score(case: EvalCase, pred: dict) -> Any:
    """直接调 rule-based 兜底, 绕开 LLM 调用."""
    j = LLMJudge()
    return j._fallback_rule_based(case, pred)


def test_judge_fallback_exact():
    case = EvalCase(id="c1", skill="s", input={}, gold={"exact": {"description": "X"}})
    out = _fallback_score(case, {"description": "X"})
    assert out.score == 1.0


def test_judge_fallback_exact_mismatch():
    case = EvalCase(id="c1", skill="s", input={}, gold={"exact": {"x": 1}})
    out = _fallback_score(case, {"x": 2})
    assert out.score == 0.0
    assert any("mismatch" in i.lower() for i in out.issues)


def test_judge_fallback_contains_full():
    case = EvalCase(id="c1", skill="s", input={}, gold={"contains": ["订单", "GMV"]})
    out = _fallback_score(case, {"description": "这是一张订单 GMV 汇总表"})
    assert out.score == 1.0


def test_judge_fallback_contains_partial():
    case = EvalCase(id="c1", skill="s", input={}, gold={"contains": ["订单", "GMV", "用户"]})
    out = _fallback_score(case, {"description": "订单 GMV 汇总表"})
    assert 0.5 < out.score < 1.0
    assert any("missing" in i.lower() for i in out.issues)


def test_judge_fallback_list_contains_must_have():
    case = EvalCase(id="c1", skill="s", input={}, gold={"must_contain": ["orders_daily", "LIMIT"]})
    out = _fallback_score(case, {"sql": "SELECT * FROM orders_daily LIMIT 10"})
    assert out.score >= 0.9


def test_judge_fallback_list_contains_forbidden_hit():
    case = EvalCase(id="c1", skill="s", input={}, gold={"must_not_contain": ["DELETE"]})
    out = _fallback_score(case, {"sql": "DELETE FROM orders_daily"})
    assert out.score <= 0.0
    assert any("forbidden" in i.lower() for i in out.issues)


def test_judge_fallback_guards_match():
    case = EvalCase(id="c1", skill="s", input={}, gold={"guards": ["schema", "sql_safety", "row_limit"]})
    out = _fallback_score(case, {"validation": {"passed_guards": ["schema", "sql_safety", "row_limit"]}})
    assert out.score == 1.0


def test_judge_fallback_guards_miss():
    case = EvalCase(id="c1", skill="s", input={}, gold={"guards": ["schema", "sql_safety", "pii"]})
    out = _fallback_score(case, {"validation": {"passed_guards": ["schema"]}})
    assert out.score < 0.7
    assert any("pii" in i for i in out.issues)


# === 2b) LLM judge 失败 fallback (模拟) ===
@pytest.mark.asyncio
async def test_judge_unavailable_falls_back_to_rule():
    from idm_api.skills.llm import LLMUnavailableError

    j = LLMJudge()
    case = EvalCase(id="c1", skill="s", input={}, gold={"contains": ["mock", "hello"]})
    fake_router = MagicMock()
    fake_router.complete = AsyncMock(side_effect=LLMUnavailableError("no key"))
    with patch("idm_api.eval.judge.get_llm_router", return_value=fake_router):
        out, model, tokens, ms = await j.score(case, {"description": "mock hello world"})
    # 兜底: contains 完全匹配 -> 1.0
    assert out.score == 1.0
    assert out.rationale == "rule-based fallback"
    assert model == "gpt-5"  # judge_model from RubricV1 default


# === 3) Gate 判定 ===
def _mk_report(avg: float, passed: int, total: int = 10) -> EvalReport:
    return EvalReport(
        skill="s",
        total=total,
        passed=passed,
        avg_score=avg,
        per_case=[EvalResult(case_id=f"c{i}", skill="s", score=avg) for i in range(total)],
    )


def test_gate_pass_no_baseline():
    rep = _mk_report(0.85, 9)
    g = gate(rep, None, GateConfig(min_avg_score=0.7, min_pass_rate=0.7))
    assert g.passed is True
    assert g.reason == "OK"


def test_gate_fail_low_avg():
    rep = _mk_report(0.5, 5)
    g = gate(rep, None, GateConfig(min_avg_score=0.7))
    assert g.passed is False
    assert "avg_score" in g.reason


def test_gate_fail_low_pass_rate():
    rep = _mk_report(0.85, 5)  # 5/10 = 50% < 70%
    g = gate(rep, None, GateConfig(min_pass_rate=0.7))
    assert g.passed is False
    assert "pass_rate" in g.reason


def test_gate_fail_regression():
    cur = _mk_report(0.65, 7)
    base = _mk_report(0.85, 9)
    g = gate(cur, base, GateConfig(max_regress=0.05, min_avg_score=0.0, min_pass_rate=0.0))
    assert g.passed is False
    assert "regress" in g.reason


def test_gate_pass_within_regress():
    cur = _mk_report(0.83, 9)
    base = _mk_report(0.85, 9)
    g = gate(cur, base, GateConfig(max_regress=0.05, min_avg_score=0.0, min_pass_rate=0.0))
    assert g.passed is True


# === 4) load_cases: 读 .jsonl ===
def test_load_cases(tmp_path: Path):
    p = tmp_path / "cases.jsonl"
    p.write_text(
        '{"id":"a","skill":"s","input":{},"gold":{}}\n'
        '# comment line, skip\n'
        '\n'
        '{"id":"b","skill":"s","input":{},"gold":{"contains":["x"]}}\n',
        encoding="utf-8",
    )
    cases = load_cases(p)
    assert len(cases) == 2
    assert cases[0].id == "a"
    assert cases[1].id == "b"


def test_load_cases_bad_json(tmp_path: Path):
    p = tmp_path / "bad.jsonl"
    p.write_text("not json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid json"):
        load_cases(p)


# === 5) CLI 解析 ===
def test_cli_build_parser():
    parser = build_parser()
    # run
    args = parser.parse_args(["run", "--skill", "infer_table_description", "--model", "gpt-5"])
    assert args.cmd == "run"
    assert args.skill == "infer_table_description"
    assert args.model == "gpt-5"
    # gate
    args = parser.parse_args(["gate", "--current", "cur.json", "--min-avg", "0.8"])
    assert args.cmd == "gate"
    assert args.min_avg == 0.8
    # list
    args = parser.parse_args(["list"])
    assert args.cmd == "list"


# === 6) SkillEvalRunner 跑 1 个 fake skill ===
@pytest.mark.asyncio
async def test_runner_runs_one_case_with_fake_skill():
    """模拟 1 个 skill, 验证 runner 能跑 + 兜底打分."""
    from idm_api.skills.registry import SkillContext, SkillOutput, SkillResult, skill

    @skill(name="__fake_skill_for_eval", version=1, agent="core")
    async def fake_skill(ctx: SkillContext, **inputs):  # noqa: ANN001
        question = inputs.get("question", "")
        return SkillResult(
            ok=True,
            output=SkillOutput(
                items=[{"description": f"mock for: {question}"}],
                summary={"q": question},
            ),
        )

    from idm_api.skills.llm import LLMUnavailableError

    fake_router = MagicMock()
    fake_router.complete = AsyncMock(side_effect=LLMUnavailableError("no key"))
    with patch("idm_api.eval.runner.get_clickhouse_mcp", return_value=object()), \
         patch("idm_api.eval.runner.get_llm_router", return_value=AsyncMock()), \
         patch("idm_api.eval.judge.get_llm_router", return_value=fake_router):
        runner = SkillEvalRunner(skill="__fake_skill_for_eval", judge_model=None)
        case = EvalCase(
            id="t1",
            skill="__fake_skill_for_eval",
            input={"question": "hello world"},
            gold={"contains": ["mock", "hello"]},
        )
        report = await runner.run([case])
        assert report.total == 1
        assert report.per_case[0].case_id == "t1"
        # 兜底打分: contains 完全匹配 -> 1.0
        assert report.per_case[0].score == 1.0
        assert report.avg_score == 1.0
        # markdown 至少有 title
        assert "# Skill Eval" in report.markdown


@pytest.mark.asyncio
async def test_runner_save(tmp_path: Path):
    """runner.save() 写 json + md."""
    from idm_api.skills.registry import SkillContext, SkillOutput, SkillResult, skill

    @skill(name="__fake_skill_for_save", version=1, agent="core")
    async def fake_skill2(ctx: SkillContext, **inputs):  # noqa: ANN001
        return SkillResult(ok=True, output=SkillOutput(items=[{"v": 1}], summary={}))

    from idm_api.skills.llm import LLMUnavailableError

    fake_router = MagicMock()
    fake_router.complete = AsyncMock(side_effect=LLMUnavailableError("no key"))
    with patch("idm_api.eval.runner.get_clickhouse_mcp", return_value=object()), \
         patch("idm_api.eval.runner.get_llm_router", return_value=AsyncMock()), \
         patch("idm_api.eval.judge.get_llm_router", return_value=fake_router):
        runner = SkillEvalRunner(skill="__fake_skill_for_save", judge_model=None)
        case = EvalCase(
            id="t1",
            skill="__fake_skill_for_save",
            input={},
            gold={"contains": ["v"]},
        )
        report = await runner.run([case])
        jpath, mpath = runner.save(report, tmp_path)
        assert jpath.exists()
        assert mpath.exists()
        data = json.loads(jpath.read_text(encoding="utf-8"))
        assert data["skill"] == "__fake_skill_for_save"
        assert data["total"] == 1


@pytest.mark.asyncio
async def test_runner_skill_exception_marks_zero():
    """skill 抛异常时, 不会让整个 runner 挂, 而是单 case score=0."""
    from idm_api.skills.registry import SkillContext, SkillOutput, SkillResult, skill

    @skill(name="__fake_skill_boom", version=1, agent="core")
    async def boom_skill(ctx: SkillContext, **inputs):  # noqa: ANN001
        raise RuntimeError("boom")

    from idm_api.skills.llm import LLMUnavailableError

    fake_router = MagicMock()
    fake_router.complete = AsyncMock(side_effect=LLMUnavailableError("no key"))
    with patch("idm_api.eval.runner.get_clickhouse_mcp", return_value=object()), \
         patch("idm_api.eval.runner.get_llm_router", return_value=AsyncMock()), \
         patch("idm_api.eval.judge.get_llm_router", return_value=fake_router):
        runner = SkillEvalRunner(skill="__fake_skill_boom", judge_model=None)
        case = EvalCase(id="t1", skill="__fake_skill_boom", input={}, gold={})
        report = await runner.run([case])
        assert report.total == 1
        assert report.per_case[0].score == 0.0
        assert report.per_case[0].error is not None
        assert "boom" in report.per_case[0].error
