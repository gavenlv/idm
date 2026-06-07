"""idm-eval CLI: 跑评估 + 门禁.

用法:
  python -m idm_api.eval.cli run --skill infer_table_description --model gpt-5
  python -m idm_api.eval.cli run --all
  python -m idm_api.eval.cli gate --baseline reports/main.json --current reports/pr.json
  python -m idm_api.eval.cli list  # 列出已注册的 skill

设计文档: docs/design/eval-harness.md §附录A
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from idm_api.eval.runner import SkillEvalRunner, gate, load_cases
from idm_api.eval.types import EvalReport, GateConfig
from idm_api.skills.registry import get_registry


CASES_DIR = Path(__file__).parent / "cases"


def _load_builtin_skills() -> None:
    """显式 import 所有 builtin skills 以触发 @skill 装饰器.

    这让 'python -m idm_api.eval.cli list' 不依赖 FastAPI lifespan 也能列出.
    """
    import importlib

    builtin_dir = Path(__file__).parent.parent / "skills" / "builtin"
    if not builtin_dir.exists():
        return
    for f in sorted(builtin_dir.glob("*.py")):
        if f.name.startswith("_") or f.name == "__init__.py":
            continue
        mod_name = f"idm_api.skills.builtin.{f.stem}"
        try:
            importlib.import_module(mod_name)
        except Exception as e:  # noqa: BLE001
            # 不让单个 import 失败炸掉整个 list
            print(f"[eval] warn: import {mod_name} failed: {e}", file=sys.stderr)


def _list_skills() -> list[dict]:
    _load_builtin_skills()
    return get_registry().list()


def _find_cases(skill: str) -> Path | None:
    """按 skill 找 .jsonl. 支持: cases/<skill>.jsonl, cases/<skill>/smoke.jsonl."""
    p1 = CASES_DIR / f"{skill}.jsonl"
    if p1.exists():
        return p1
    p2 = CASES_DIR / skill / "smoke.jsonl"
    if p2.exists():
        return p2
    return None


async def cmd_run(args: argparse.Namespace) -> int:
    # 关键: 先 import builtin skills, 装饰器才注册成功
    _load_builtin_skills()
    skills = _list_skills() if args.all else [{"name": args.skill}]
    if not skills:
        print("[eval] no skills to run", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[dict] = []

    for s in skills:
        skill_name = s["name"] if isinstance(s, dict) else s["name"]
        cases_path = _find_cases(skill_name)
        if not cases_path:
            print(f"[eval] skip {skill_name}: no cases file under {CASES_DIR}")
            continue
        print(f"[eval] {skill_name}: loading cases from {cases_path}")
        cases = load_cases(cases_path)
        if not cases:
            print(f"[eval] skip {skill_name}: 0 cases")
            continue

        # 按 tag 过滤
        if args.tag:
            tags = {t.strip() for t in args.tag.split(",") if t.strip()}
            before = len(cases)
            cases = [c for c in cases if c.tags and (set(c.tags) & tags)]
            if not cases:
                print(f"[eval] skip {skill_name}: 0 cases match tag(s)={tags} (was {before})")
                continue
            print(f"[eval] {skill_name}: filtered to {len(cases)} cases by tag(s)={tags}")

        runner = SkillEvalRunner(
            skill=skill_name,
            model=args.model,
            judge_model=args.judge_model,
            parallel=args.parallel,
        )
        report = await runner.run(cases)
        jpath, mpath = runner.save(report, out_dir)
        print(
            f"[eval] {skill_name}: avg={report.avg_score} pass={report.passed}/{report.total} "
            f"-> {jpath.name} + {mpath.name}"
        )
        summaries.append(
            {
                "skill": skill_name,
                "avg_score": report.avg_score,
                "passed": report.passed,
                "total": report.total,
                "result_json": str(jpath),
                "report_md": str(mpath),
            }
        )

    if not summaries:
        print("[eval] no reports produced", file=sys.stderr)
        return 1

    summary_path = out_dir / "_summary.json"
    summary_path.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[eval] summary: {summary_path}")
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    current = EvalReport.model_validate_json(Path(args.current).read_text(encoding="utf-8"))
    baseline = None
    if args.baseline:
        baseline = EvalReport.model_validate_json(Path(args.baseline).read_text(encoding="utf-8"))
    cfg = GateConfig(
        min_avg_score=args.min_avg,
        min_pass_rate=args.min_pass,
        max_regress=args.max_regress,
    )
    g = gate(current, baseline, cfg)
    print(f"[gate] passed={g.passed}  reason={g.reason}")
    print(f"[gate] report_avg={g.report_avg}  baseline_avg={g.baseline_avg}  pass_rate={g.report_pass_rate:.2%}")
    return 0 if g.passed else 2


def cmd_list(_args: argparse.Namespace) -> int:
    skills = _list_skills()
    print(f"{len(skills)} skills registered:")
    for s in skills:
        print(f"  - {s['name']:35s} v{s['version']}  agent={s['agent']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="idm-eval", description="IDM Skill Eval Harness")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="跑 1 个或全部 skill 的 gold cases")
    p_run.add_argument("--skill", help="单个 skill 名")
    p_run.add_argument("--all", action="store_true", help="跑所有有 cases 文件的 skill")
    p_run.add_argument("--model", default="gpt-5", help="skill 用哪个 LLM (默认 gpt-5)")
    p_run.add_argument("--judge-model", default="gpt-5", help="judge 用哪个 LLM")
    p_run.add_argument("--parallel", type=int, default=3)
    p_run.add_argument("--out", default="reports/", help="输出目录")
    p_run.add_argument(
        "--tag",
        help="按 tag 过滤, 逗号分隔 (例: smoke,nl2sql-security)",
    )
    p_run.set_defaults(func=cmd_run)

    p_gate = sub.add_parser("gate", help="门禁: 对比 baseline, 决定 pass/fail")
    p_gate.add_argument("--current", required=True, help="当前 report JSON")
    p_gate.add_argument("--baseline", help="baseline report JSON (可选)")
    p_gate.add_argument("--min-avg", type=float, default=0.7)
    p_gate.add_argument("--min-pass", type=float, default=0.7)
    p_gate.add_argument("--max-regress", type=float, default=0.05)
    p_gate.set_defaults(func=cmd_gate)

    p_list = sub.add_parser("list", help="列出已注册的 skill")
    p_list.set_defaults(func=cmd_list)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if asyncio.iscoroutinefunction(args.func):
        return asyncio.run(args.func(args))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
