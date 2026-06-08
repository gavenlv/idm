"""analyze_data_pipeline: 端到端 6 阶段编排 (GCS→AF+FK→GCS→MEX→GCS→FK2→CH→Superset).

适用 6 阶段真实管道 (强约束):
  1. GCS raw + Airflow DAG + Flink preprocess
  2. GCS model-input  (Flink 写出)
  3. MEX 黑盒 (io.yaml)
  4. GCS model-output (MEX 写出)
  5. Flink load + ClickHouse
  6. Superset (Dashboard/Chart/Dataset)

Inputs:
    use_case: dict           # 含 sources 列表 (gcs / github / superset_export / clickhouse)
    apply: bool = True       # True=直接写 lineage, False=仅收集
    stages: list[str]        # 自定义阶段执行顺序 (默认按 1->6 全部)
    default_stages: list[str] = [
        "discover_gcs_assets(raw)",
        "parse_airflow_dag",
        "parse_flink_job(preprocess)",
        "discover_gcs_assets(model-input)",
        "parse_mex_io",
        "discover_gcs_assets(model-output)",
        "parse_flink_job(load_ch)",
        "discover_clickhouse_assets",
        "parse_superset_dashboard",
    ]

Outputs (SkillOutput.items):
    [{stage, skill, from_fqn, to_fqn, transform_subtype, source, confidence, ok, summary}, ...]

流程:
    1) parse use_case.sources, 按 stage 排序
    2) 对每个 source 调对应的子 Skill (带 stage 参数)
    3) 收集所有产出的 fqn / lineage 边
    4) 端到端串图: gcs_raw -> airflow_task -> flink_job -> gcs_mi -> mex_io -> gcs_mo -> flink_load -> ch_table -> superset
    5) 汇总返回 edges + stage_coverage
"""
from __future__ import annotations

import logging
from typing import Any

from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill

logger = logging.getLogger(__name__)


# === 6 阶段默认执行顺序 (skill 层面) ===
DEFAULT_STAGES: list[str] = [
    "discover_gcs_assets(raw)",          # 1.1: 上游 GCS
    "parse_airflow_dag",                  # 1.2: Airflow DAG
    "parse_flink_job(preprocess)",        # 1.3: Flink 预处理
    "discover_gcs_assets(model-input)",   # 2:   GCS model-input (Flink 写出)
    "parse_mex_io",                       # 3:   MEX 黑盒
    "discover_gcs_assets(model-output)",  # 4:   GCS model-output (MEX 写出)
    "parse_flink_job(load_ch)",           # 5.1: Flink 加载
    "discover_clickhouse_assets",         # 5.2: ClickHouse 扫描
    "parse_superset_dashboard",           # 6:   Superset Report
]


def _parse_skill_call(stage: str) -> tuple[str, dict[str, Any]]:
    """'discover_gcs_assets(raw)' -> ('discover_gcs_assets', {'source_role': 'raw'})."""
    if "(" in stage and stage.endswith(")"):
        name, args = stage.split("(", 1)
        name = name.strip()
        args = args[:-1].strip()
        out: dict[str, Any] = {}
        for kv in args.split(","):
            if "=" in kv:
                k, v = kv.split("=", 1)
                out[k.strip()] = v.strip()
            else:
                out[kv.strip()] = True
        return name, out
    return stage, {}


def _select_source(sources: list[dict[str, Any]], skill_name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """按 type / stage 找最匹配的 source. 找不到返 None."""
    if not sources:
        return None
    # 显式 stage 优先
    wanted_stage = args.get("stage")
    # 1) 按 stage 过滤
    candidates: list[dict[str, Any]] = []
    for src in sources:
        if wanted_stage is not None and src.get("stage") is not None:
            if int(src.get("stage")) != int(wanted_stage):
                continue
        candidates.append(src)
    if not candidates:
        candidates = sources
    # 2) 按 skill_name 找 type
    type_map = {
        "discover_gcs_assets": "gcs",
        "parse_flink_job": "github",
        "parse_airflow_dag": "github",
        "parse_mex_io": "github",
        "discover_clickhouse_assets": "clickhouse",
        "parse_superset_dashboard": None,  # 接受 superset_export / superset_db
    }
    wanted_type = type_map.get(skill_name)
    if skill_name == "parse_superset_dashboard":
        for src in candidates:
            if src.get("type") in ("superset_export", "superset_db"):
                return src
        return candidates[0] if candidates else None
    for src in candidates:
        if wanted_type and src.get("type") == wanted_type:
            return src
    return candidates[0] if candidates else None


@skill(name="analyze_data_pipeline", version=2, agent="lineage")
async def analyze_data_pipeline(ctx: SkillContext, **inputs: Any) -> SkillResult:
    use_case: dict = inputs.get("use_case") or {}
    apply: bool = bool(inputs.get("apply", True))
    stages: list[str] = inputs.get("stages") or DEFAULT_STAGES

    if not use_case:
        return SkillResult(ok=False, output=SkillOutput(), error="use_case is required")
    if ctx.db is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.db is None")

    sources = use_case.get("sources") or []
    if not sources:
        return SkillResult(ok=False, output=SkillOutput(), error="use_case.sources is required")

    # 1) 按 stage 顺序调用各 skill
    from idm_api.skills.runner import run_skill

    out_items: list[dict[str, Any]] = []
    stage_results: dict[str, Any] = {}
    stage_coverage: dict[int, list[str]] = {i: [] for i in range(1, 7)}

    for stage_label in stages:
        skill_name, default_args = _parse_skill_call(stage_label)
        # 找匹配的 source
        matched_src = _select_source(sources, skill_name, default_args)
        if matched_src is None:
            logger.info("analyze_data_pipeline: stage %s no source matched, skip", stage_label)
            continue

        cfg = matched_src.get("config") or {}
        # 合并: default_args < cfg < apply
        skill_inputs = {**default_args, **cfg, "apply": apply}
        # 自动注入 stage (按 source 或 default_args)
        if "stage" not in skill_inputs:
            if matched_src.get("stage") is not None:
                skill_inputs["stage"] = int(matched_src["stage"])
            else:
                # 从 stage_label 推断 (1|2|3|4|5|6)
                # parse_superset_dashboard 默认 6, parse_airflow_dag 默认 1, parse_mex_io 默认 3
                if skill_name == "parse_superset_dashboard":
                    skill_inputs["stage"] = 6
                elif skill_name == "parse_airflow_dag":
                    skill_inputs["stage"] = 1
                elif skill_name == "parse_mex_io":
                    skill_inputs["stage"] = 3

        # 跟踪阶段覆盖
        s_val = skill_inputs.get("stage")
        if isinstance(s_val, int) and 1 <= s_val <= 6:
            stage_coverage[s_val].append(skill_name)

        try:
            sub = await run_skill(skill_name, skill_inputs, db=ctx.db, use_case_id=ctx.use_case_id)
            stage_results[stage_label] = {
                "ok": sub.ok,
                "summary": sub.output.summary if sub.ok else None,
                "error": sub.error,
                "stage": s_val,
            }
            for it in sub.output.items or []:
                out_items.append({"stage_label": stage_label, "stage": s_val, "skill": skill_name, **it})
        except Exception as e:  # noqa: BLE001
            logger.warning("analyze_data_pipeline: stage %s failed: %s", stage_label, e)
            stage_results[stage_label] = {"ok": False, "error": str(e)[:200], "stage": s_val}

    # 2) 端到端覆盖检查: 6 个阶段是否都触达
    covered = sorted([s for s, skills in stage_coverage.items() if skills])
    missing_stages = [i for i in range(1, 7) if not stage_coverage[i]]
    coverage_pct = round(100 * len(covered) / 6, 1)

    return SkillResult(
        ok=True,
        output=SkillOutput(
            items=out_items,
            summary={
                "stages_executed": list(stage_results.keys()),
                "stage_results": stage_results,
                "edges_total": len(out_items),
                "use_case_id": use_case.get("id"),
                "stage_coverage": stage_coverage,
                "covered_stages": covered,
                "missing_stages": missing_stages,
                "coverage_pct": coverage_pct,
                "apply": apply,
            },
        ),
    )
