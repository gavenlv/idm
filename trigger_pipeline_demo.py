"""trigger_pipeline_demo.py — 触发 6 阶段数据管道端到端加载 (一次性脚本).

用法:
    # 1) 先在 .env 里设置 MOCK_GCS_ROOT / MOCK_GITHUB_ROOT
    # 2) 起 API (任意端口, 8000):
    #    cd apps/api && uv run --no-progress uvicorn idm_api.main:app --port 8000
    # 3) 跑本脚本:
    #    python trigger_pipeline_demo.py --api http://localhost:8000
    #    或: python trigger_pipeline_demo.py --api http://localhost:8000 --stage 1
    # 4) 重扫:
    #    python trigger_pipeline_demo.py --api http://localhost:8000 --rescan

设计:
    - 调用 IDM API 的 /api/v1/skills/run 端点 (同步)
    - 默认全 6 阶段 (用 analyze_data_pipeline skill 一次过)
    - 也支持单步 / 任意子集 (--stage 1|2|3|4|5|6)
    - 失败不退出, 继续跑, 最后统一报告
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent

USE_CASE_PATH = ROOT / "use_cases" / "shop-orders-mex-pipeline.yml"


def load_use_case() -> dict[str, Any]:
    """读 use_case YAML (直接用 yaml.safe_load, 避免依赖 aio)."""
    import yaml
    with open(USE_CASE_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def post_skill(
    client: httpx.Client,
    base: str,
    skill_name: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """POST /api/v1/skills/run {name, inputs} → 返回 SkillResult dict."""
    url = f"{base}/api/v1/skills/run"
    r = client.post(url, json={"name": skill_name, "inputs": inputs}, timeout=60.0)
    r.raise_for_status()
    return r.json()


def trigger_full_pipeline(client: httpx.Client, base: str, use_case: dict[str, Any]) -> dict[str, Any]:
    """一次跑完 6 阶段 (analyze_data_pipeline)."""
    return post_skill(
        client, base,
        skill_name="analyze_data_pipeline",
        inputs={"use_case": use_case, "apply": True},
    )


def trigger_stage(
    client: httpx.Client,
    base: str,
    use_case: dict[str, Any],
    stage: int,
) -> dict[str, Any]:
    """按阶段调对应 skill."""
    src_by_stage: dict[int, dict[str, Any]] = {s["stage"]: s for s in use_case["sources"] if s.get("stage") is not None}
    src = src_by_stage.get(stage)
    if src is None:
        return {"ok": False, "error": f"no source for stage {stage}"}

    sid = src["id"]
    if src["type"] == "gcs":
        return post_skill(
            client, base,
            skill_name="discover_gcs_assets",
            inputs={
                "bucket": src["config"]["bucket"],
                "prefix": src["config"].get("prefix", ""),
                "stage": stage,
                "source_role": {1: "raw", 2: "model_input", 4: "model_output"}.get(stage),
                "apply": True,
            },
        )
    if src["type"] == "github":
        cfg = src["config"]
        if sid == "gh-airflow":
            # Airflow 用本地文件
            dag_path = ROOT / "fixtures" / "pipeline-demo" / "github" / "company" / "dwh" / "dags" / "etl_orders_daily.py"
            return post_skill(
                client, base,
                skill_name="parse_airflow_dag",
                inputs={
                    "dag_file_path": str(dag_path),
                    "stage": 1,
                    "apply": True,
                },
            )
        if sid.startswith("gh-flink"):
            sub = "preprocess" if stage == 1 else "load_ch"
            paths = src["scope"]["paths"]
            return post_skill(
                client, base,
                skill_name="parse_flink_job",
                inputs={
                    "repo": cfg["repo"],
                    "paths": paths,
                    "stage": stage,
                    "transform_subtype": sub,
                    "apply": True,
                },
            )
        if sid == "gh-mex":
            paths = src["scope"]["paths"]
            return post_skill(
                client, base,
                skill_name="parse_mex_io",
                inputs={
                    "repo": cfg["repo"],
                    "paths": paths,
                    "pipeline_stage": 3,
                    "apply": True,
                },
            )
    if src["type"] == "clickhouse":
        return post_skill(
            client, base,
            skill_name="discover_clickhouse_assets",
            inputs={
                "database": src["config"]["database"],
                "include_tables": src.get("scope", {}).get("include_tables", []),
            },
        )
    if src["type"] == "superset_export":
        return post_skill(
            client, base,
            skill_name="parse_superset_dashboard",
            inputs={
                "stage": 6,
                "service_name": "superset-demo",
                "include_charts": True,
                "include_datasets": True,
                "apply": True,
            },
        )
    return {"ok": False, "error": f"unknown source type: {src['type']}"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Trigger IDM 6-stage pipeline demo")
    parser.add_argument("--api", default="http://localhost:8000", help="IDM API base URL")
    parser.add_argument("--stage", type=int, choices=[1, 2, 3, 4, 5, 6], help="single stage only")
    parser.add_argument("--rescan", action="store_true", help="re-run same pipeline (idempotent)")
    parser.add_argument("--use-case", default=str(USE_CASE_PATH), help="path to use case YAML")
    args = parser.parse_args()

    print(f"[1/3] Loading use case: {args.use_case}")
    global USE_CASE_PATH
    USE_CASE_PATH = Path(args.use_case)
    use_case = load_use_case()
    print(f"      → id={use_case['id']}, stages={[s.get('stage') for s in use_case['sources']]}")

    print(f"[2/3] Pinging API: {args.api}/health/ready")
    t0 = time.time()
    with httpx.Client() as client:
        r = client.get(f"{args.api}/health/ready", timeout=10.0)
        r.raise_for_status()
        print(f"      → ready: {r.json()}")

        print(f"[3/3] Triggering pipeline (rescan={args.rescan})")
        if args.stage:
            print(f"      → single stage: {args.stage}")
            res = trigger_stage(client, args.api, use_case, args.stage)
        else:
            print("      → full 6-stage analyze_data_pipeline")
            res = trigger_full_pipeline(client, args.api, use_case)

        print(json.dumps(res, indent=2, ensure_ascii=False)[:4000])

    print(f"\nDone in {time.time() - t0:.1f}s")
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
