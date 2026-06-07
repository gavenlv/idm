"""verify_s1_12.py: M1 S1.12 验证 (Lineage 可视化)."""
import sys
import httpx

BASE_WEB = "http://127.0.0.1:5173"
BASE_API = "http://127.0.0.1:8080/api/v1"
failed: list[str] = []


def check(c: bool, label: str) -> None:
    if c:
        print(f"    [OK] {label}")
    else:
        print(f"    [FAIL] {label}")
        failed.append(label)


print("=" * 60)
print("M1 S1.12 验证: Lineage 可视化 (React Flow)")
print("=" * 60)

# 1) API: 拉一张表
with httpx.Client(base_url=BASE_API, timeout=30.0) as c:
    r = c.get("/assets", params={"limit": 5})
    assets = r.json().get("items", [])
check(len(assets) > 0, f"API /assets returns {len(assets)} items")
if not assets:
    sys.exit(1)
asset = assets[0]

# 2) 拉 lineage
with httpx.Client(base_url=BASE_API, timeout=30.0) as c:
    r = c.get(f"/assets/{asset['id']}/lineage", params={"depth": 3})
    g = r.json()
print(f"    center: {g['center_fqn']}, upstream={len(g['upstream'])}, downstream={len(g['downstream'])}, nodes={len(g['nodes'])}, edges={len(g['edges'])}")
check(g.get("center_fqn") == asset["fqn"], "center_fqn matches")
check("upstream" in g and "downstream" in g, "upstream/downstream in response")
check("nodes" in g and "edges" in g, "nodes/edges in response")

# 3) 路由
with httpx.Client(base_url=BASE_WEB, timeout=10.0) as c:
    r = c.get("/lineage")
check(r.status_code == 200, f"GET /lineage (HTTP {r.status_code})")

# 4) Web dev server: 检查编译通过 (vite 会在 5xx 报模块错误)
import re
errs = re.findall(r"Pre-transform error|Transform failed|Cannot find", r.text)
check(len(errs) == 0, f"no transform errors (got: {errs[:3]})")

# 5) 检查 LineagePage 编译入口
with httpx.Client(base_url=BASE_WEB, timeout=10.0) as c:
    r = c.get("/src/pages/LineagePage.tsx")
    # vite 编译后返回 JS
    check(r.status_code == 200, f"LineagePage.tsx compiles (HTTP {r.status_code})")

print("\n" + "=" * 60)
if failed:
    print(f"M1 S1.12 FAIL: {len(failed)} checks failed")
    for f in failed:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("M1 S1.12 PASS")
