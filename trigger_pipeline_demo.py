"""trigger_pipeline_demo.py — 触发 6 阶段数据管道端到端加载 (一次性脚本).

用法:
    # 1) 先在 .env 里设置 MOCK_GCS_ROOT / MOCK_GITHUB_ROOT
    # 2) 起 API (任意端口, 8000):
    #    cd apps/api && uv run --no-progress uvicorn idm_api.main:app --port 8000
    # 3) 跑本脚本:
    #    python trigger_pipeline_demo.py --api http://localhost:8000
    #    或: python trigger_pipeline_demo.py --api http://localhost:8000 --stage 1
    # 4) 重扫 (idempotent):
    #    python trigger_pipeline_demo.py --api http://localhost:8000 --rescan
    # 5) 系统级 rescan (不依赖 use case):
    #    python trigger_pipeline_demo.py --api http://localhost:8000 --sys-rescan gcs --bucket company-raw

设计 (M1.5):
    - 默认走 IDM 系统的 /api/v1/use-cases/{id}/trigger 端点 (业务入口)
    - 可选 /api/v1/use-cases/{id}/rescan (alias, 语义化)
    - 可选 /api/v1/scan/asset (系统级, 不带 use case)
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


def post_json(
    client: httpx.Client,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    r = client.post(url, json=payload or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()


# === 模式 A: use case 触发 ===
def trigger_full_pipeline(client: httpx.Client, base: str, uc_id: str) -> dict[str, Any]:
    return post_json(
        client,
        f"{base}/api/v1/use-cases/{uc_id}/trigger",
        payload={"use_case_id": uc_id, "apply": True},
    )


def trigger_stage(client: httpx.Client, base: str, uc_id: str, stage: int) -> dict[str, Any]:
    return post_json(
        client,
        f"{base}/api/v1/use-cases/{uc_id}/stages/{stage}/trigger",
        payload={"stage": stage},
    )


def rescan_use_case(client: httpx.Client, base: str, uc_id: str) -> dict[str, Any]:
    return post_json(
        client,
        f"{base}/api/v1/use-cases/{uc_id}/rescan",
        payload={"use_case_id": uc_id, "apply": True},
    )


# === 模式 B: 系统级 rescan ===
def sys_rescan(client: httpx.Client, base: str, source_type: str, **kw: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"source_type": source_type}
    payload.update({k: v for k, v in kw.items() if v is not None})
    return post_json(
        client,
        f"{base}/api/v1/scan/asset",
        payload=payload,
    )


# === Backward compat: 旧脚本会调 post_skill ===
def post_skill(
    client: httpx.Client,
    base: str,
    skill_name: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    return post_json(
        client,
        f"{base}/api/v1/skills/run",
        payload={"name": skill_name, "inputs": inputs},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Trigger IDM 6-stage pipeline demo")
    parser.add_argument("--api", default="http://localhost:8000", help="IDM API base URL")
    parser.add_argument("--stage", type=int, choices=[1, 2, 3, 4, 5, 6], help="single stage only")
    parser.add_argument("--rescan", action="store_true", help="re-run same pipeline (idempotent)")
    parser.add_argument("--use-case", default=str(USE_CASE_PATH), help="path to use case YAML")
    parser.add_argument(
        "--sys-rescan",
        choices=["gcs", "clickhouse", "superset_export", "all"],
        help="system-level rescan (no use case)",
    )
    parser.add_argument("--bucket", help="GCS bucket for --sys-rescan gcs")
    parser.add_argument("--database", help="ClickHouse database for --sys-rescan clickhouse")
    parser.add_argument(
        "--service-name", default="superset-demo", help="Superset service name for sys-rescan",
    )
    args = parser.parse_args()

    global USE_CASE_PATH
    USE_CASE_PATH = Path(args.use_case)
    uc_id = load_use_case()["id"]

    print(f"[1/3] use case: {uc_id}  (file: {args.use_case})")

    print(f"[2/3] Pinging API: {args.api}/health/ready")
    t0 = time.time()
    with httpx.Client() as client:
        r = client.get(f"{args.api}/health/ready", timeout=10.0)
        r.raise_for_status()
        print(f"      → ready: {r.json()}")

        print(f"[3/3] Triggering (rescan={args.rescan}, stage={args.stage}, sys={args.sys_rescan})")
        if args.sys_rescan:
            res = sys_rescan(
                client, args.api, args.sys_rescan,
                bucket=args.bucket, database=args.database, service_name=args.service_name,
            )
        elif args.rescan:
            res = rescan_use_case(client, args.api, uc_id)
        elif args.stage:
            res = trigger_stage(client, args.api, uc_id, args.stage)
        else:
            res = trigger_full_pipeline(client, args.api, uc_id)

        print(json.dumps(res, indent=2, ensure_ascii=False)[:4000])

    print(f"\nDone in {time.time() - t0:.1f}s")
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
