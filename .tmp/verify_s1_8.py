"""M1 S1.8 验证: parse_superset_dashboard Skill.

依赖:
- mock superset 跑在 127.0.0.1:9088
- ch-shop_dw 资产存在 (来自 S1.6 / parse_dbt_manifest)

流程:
0) 启动 mock superset (本进程子线程), 设置 .env.superset_url
1) dry-run: 解析不写库
2) 真跑: 写入 KG, 应生成 dashboard/chart/dataset 资产 + lineage
3) 校验: dashboard 资产 fqn superset.dashboard.1, chart 资产 fqn superset.chart.10,
   dataset 资产 fqn superset.ds.100, 并存在 chart -> dataset -> table 两条 lineage.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

API = "http://127.0.0.1:8080/api/v1"
MOCK_PORT = 9088
MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"


# 1) 启 mock superset
sys.path.insert(0, str(Path(__file__).parent))
import mock_superset  # noqa: E402

mock_superset.start(MOCK_PORT)
time.sleep(0.5)

# 2) 设置 .env 超参 (本进程 env)
os.environ["SUPERSET_URL"] = MOCK_URL
os.environ["SUPERSET_USERNAME"] = "admin"
os.environ["SUPERSET_PASSWORD"] = "admin"
os.environ["SUPERSET_VERIFY_SSL"] = "false"

import httpx  # noqa: E402

passed: list[str] = []
failed: list[str] = []


def check(cond: bool, label: str) -> None:
    if cond:
        passed.append(label)
        print(f"    [OK] {label}")
    else:
        failed.append(label)
        print(f"    [FAIL] {label}")


def get(path: str, **params) -> tuple[int, dict]:
    r = httpx.get(f"{API}{path}", params=params, timeout=20)
    try:
        return r.status_code, r.json()
    except Exception:  # noqa: BLE001
        return r.status_code, {}


def post(path: str, body: dict) -> tuple[int, dict]:
    r = httpx.post(f"{API}{path}", json=body, timeout=60)
    try:
        return r.status_code, r.json()
    except Exception:  # noqa: BLE001
        return r.status_code, {}


def main():
    print("=" * 60)
    print("M1 S1.8 验证: parse_superset_dashboard Skill (mock superset)")
    print("=" * 60)

    # 0) 技能注册
    code, h = get("/skills")
    check(code == 200, "GET /skills (200)")
    skill_names = [s["name"] for s in h.get("items", [])]
    check("parse_superset_dashboard" in skill_names, "parse_superset_dashboard registered")

    # 0.1) 确认 dbt-shop_dw 资产存在
    code, a = get("/assets", service="dbt-shop_dw", q="fct_orders_daily", limit=5)
    fct = next((x for x in a.get("items", []) if x.get("name") == "fct_orders_daily"), None)
    check(fct is not None, "dbt-shop_dw.fct_orders_daily exists (prereq from S1.6)")

    # 1) dry-run
    print("\n[1] dry-run")
    code, r = post("/skills/run", {
        "name": "parse_superset_dashboard",
        "inputs": {"dashboard_ids": [1, 2], "include_charts": True, "include_datasets": True},
        "dry_run": True,
    })
    check(code == 200 and r.get("ok"), f"dry-run ok (ok={r.get('ok')}, err={r.get('error')})")
    s1 = (r.get("output", {}).get("summary", {}) or r.get("summary", {}) or {})
    print(f"    summary: {json.dumps({k: s1.get(k) for k in ('superset_reachable', 'dashboards_seen', 'charts_seen', 'datasets_seen')}, ensure_ascii=False)}")
    check(s1.get("superset_reachable") is True, "superset reachable")
    check(s1.get("dashboards_seen") == 2, f"dashboards_seen==2 (got {s1.get('dashboards_seen')})")
    check(s1.get("charts_seen", 0) >= 3, f"charts_seen>=3 (got {s1.get('charts_seen')})")
    check(s1.get("datasets_seen", 0) >= 2, f"datasets_seen>=2 (got {s1.get('datasets_seen')})")

    # 2) 真跑
    print("\n[2] 真跑: 写入 KG")
    code, r2 = post("/skills/run", {
        "name": "parse_superset_dashboard",
        "inputs": {"dashboard_ids": [1, 2], "include_charts": True, "include_datasets": True},
        "dry_run": False,
    })
    check(code == 200 and r2.get("ok"), f"real run ok (ok={r2.get('ok')}, err={r2.get('error')})")
    s2 = (r2.get("output", {}).get("summary", {}) or r2.get("summary", {}) or {})
    print(f"    summary: {json.dumps({k: s2.get(k) for k in ('dashboard_assets', 'chart_assets', 'dataset_assets', 'lineage_edges_added', 'errors')}, ensure_ascii=False)}")
    check(s2.get("dashboard_assets", 0) >= 2, f"dashboard_assets>=2 (got {s2.get('dashboard_assets')})")
    check(s2.get("chart_assets", 0) >= 3, f"chart_assets>=3 (got {s2.get('chart_assets')})")
    check(s2.get("dataset_assets", 0) >= 2, f"dataset_assets>=2 (got {s2.get('dataset_assets')})")
    # lineage_edges_added 计数在 on_conflict_do_nothing 下不准确, 改通过下游校验
    edges_warn = s2.get("lineage_edges_added", 0)

    # 3) 校验 资产
    print("\n[3] 校验: superset 资产")
    code, dash_a = get("/assets", service="superset", q="dashboard.1", limit=5)
    check(any("dashboard.1" in (x.get("fqn") or "") for x in dash_a.get("items", [])), "dashboard.1 asset exists")

    code, chart_a = get("/assets", service="superset", q="chart.10", limit=5)
    check(any("chart.10" in (x.get("fqn") or "") for x in chart_a.get("items", [])), "chart.10 asset exists")

    code, ds_a = get("/assets", service="superset", q="ds.100", limit=5)
    check(any("ds.100" in (x.get("fqn") or "") for x in ds_a.get("items", [])), "ds.100 asset exists")

    # 4) 校验 lineage: chart -> dataset
    code, ds_assets = get("/assets", service="superset", q="ds.101", limit=5)
    ds101 = next((x for x in ds_assets.get("items", []) if x.get("fqn", "").endswith("ds.101")), None)
    if ds101:
        code, lin = get(f"/assets/{ds101['id']}/lineage", depth=1)
        # lineage response edges 形如 {upstream_fqn, downstream_fqn, ...}
        upstream = [e for e in lin.get("upstream", []) if "superset.chart" in (e.get("upstream_fqn") or "")]
        check(len(upstream) >= 1, f"ds.101 has chart upstream (got {len(upstream)})")

    # 5) 校验 lineage: dataset -> ch/dbt table
    if ds101:
        code, lin2 = get(f"/assets/{ds101['id']}/lineage", depth=1)
        downstream = [e for e in lin2.get("downstream", []) if ("ch-shop_dw" in (e.get("downstream_fqn") or "") or "dbt-shop_dw" in (e.get("downstream_fqn") or ""))]
        check(len(downstream) >= 1, f"ds.101 has ch/dbt table downstream (got {len(downstream)})")

    print("\n" + "=" * 60)
    if failed:
        print(f"M1 S1.8 FAIL: {len(failed)} checks failed")
        for f in failed:
            print(f"  - {f}")
        sys.exit(1)
    print("M1 S1.8 PASS")
    print("=" * 60)


if __name__ == "__main__":
    main()
