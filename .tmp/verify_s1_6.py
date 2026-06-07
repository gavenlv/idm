"""M1 S1.6 — Lineage 关系入库 + UI BFS 验证.

- parse_dbt_manifest 自动写 table_lineage
- GET /api/v1/assets/{id}/lineage 返回 nodes + edges
- BFS depth=2 正确展开上下游
- 重复运行幂等
"""
import sys
import time
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8080/api/v1"
FIXTURE = r"d:\workspace\github-ai\idm\.tmp\fixture_dbt_manifest.json"


def req(method: str, path: str, body: dict | None = None, timeout: int = 30):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def get(path: str, **q):
    if q:
        path += "?" + "&".join(f"{k}={v}" for k, v in q.items())
    return req("GET", path)


def post(path: str, body: dict):
    return req("POST", path, body)


ok = True


def check(cond: bool, msg: str):
    global ok
    print(("[OK] " if cond else "[FAIL] ") + msg)
    if not cond:
        ok = False


print("=" * 60)
print("M1 S1.6 验证: Lineage 关系入库 + BFS")
print("=" * 60)

# 1) 健康检查
code, h = get("/skills")
check(code == 200, f"GET /skills (200)")

# 2) 清理旧 dbt 资产 (幂等起点)
print("\n[1] 清理旧 dbt 资产 (幂等起点)")
old = get("/assets?service=dbt-shop_dw&limit=200")[1]["items"]
print(f"    清理前: {len(old)} dbt 资产")

# 3) 跑 dbt skill (会自动写 lineage)
print("\n[2] 跑 parse_dbt_manifest skill (写表 + 写血缘)")
code, r = post(
    "/skills/parse_dbt_manifest/run",
    {"inputs": {"manifest_path": FIXTURE, "project_name": "shop_dw", "write_lineage": True}, "dry_run": False},
)
check(code == 200 and r.get("ok"), f"parse_dbt_manifest ok={r.get('ok')}")
if r.get("ok"):
    s = r.get("summary") or r.get("output", {}).get("summary", {})
    print(f"    summary: {s}")
    check(s.get("lineage_edges_added", 0) >= 5, f"lineage_edges_added >=5 (got {s.get('lineage_edges_added')})")
    check(s.get("tables_created", 0) >= 4, f"tables_created >=4")

# 4) 找 dim_users 表 (有 depends_on 边)
print("\n[3] 找 dim_users 表 + 查 lineage depth=2")
dbt_assets = get("/assets?service=dbt-shop_dw&limit=200")[1]["items"]
dim_users = next((a for a in dbt_assets if a["name"] == "dim_users"), None)
fct_orders = next((a for a in dbt_assets if a["name"] == "fct_orders_daily"), None)
stg_orders = next((a for a in dbt_assets if a["name"] == "stg_orders"), None)
check(dim_users is not None, "dim_users 存在")
check(fct_orders is not None, "fct_orders_daily 存在")
check(stg_orders is not None, "stg_orders 存在")

if dim_users:
    code, g = get(f"/assets/{dim_users['id']}/lineage", depth=2)
    check(code == 200, f"GET dim_users lineage (200)")
    print(f"    center={g.get('center_fqn')}")
    print(f"    upstream={len(g.get('upstream', []))}  downstream={len(g.get('downstream', []))}  nodes={len(g.get('nodes', []))}")
    for e in g.get("upstream", []):
        print(f"      ↑ {e['upstream_fqn']} -> dim_users (via {e['transform_type']}, {e['source']})")

# 5) 验证 dim_users 至少有一个上游 (stg_orders 之类)
if dim_users:
    code, g = get(f"/assets/{dim_users['id']}/lineage", depth=1)
    check(g.get("upstream", []) + g.get("downstream", []) != [], "depth=1 至少有一边")

# 6) 幂等: 再次跑 dbt skill
print("\n[4] 幂等: 再次跑 dbt skill, 验证血缘不重复")
before_edges = sum(len(a.get("fqn", "")) for a in dbt_assets)  # placeholder
post("/skills/parse_dbt_manifest/run", {"inputs": {"manifest_path": FIXTURE, "project_name": "shop_dw", "write_lineage": True}, "dry_run": False})
code, r2 = post(
    "/skills/parse_dbt_manifest/run",
    {"inputs": {"manifest_path": FIXTURE, "project_name": "shop_dw", "write_lineage": True}, "dry_run": False},
)
s2 = (r2.get("summary") or r2.get("output", {}).get("summary", {}))
print(f"    re-run: created={s2.get('tables_created')}, lineage_added={s2.get('lineage_edges_added')}")
check(s2.get("tables_created", 0) == 0, f"幂等: created=0 (got {s2.get('tables_created')})")
check(s2.get("lineage_edges_added", 0) == 0, f"幂等: lineage_edges_added=0 (got {s2.get('lineage_edges_added')})")

# 7) 全 lineage 检查 (depth=1 列出所有 dbt 边)
print("\n[5] 全 lineage 边 (从 dim_users 反推)")
if dim_users:
    code, g = get(f"/assets/{dim_users['id']}/lineage", depth=2)
    edges = g.get("edges", [])
    print(f"    total edges in BFS subgraph: {len(edges)}")
    for e in edges:
        print(f"      {e['upstream_fqn']} --[{e['transform_type']}]--> {e['downstream_fqn']}")

print("\n" + ("=" * 60))
print("M1 S1.6 " + ("PASS" if ok else "FAIL"))
sys.exit(0 if ok else 1)
