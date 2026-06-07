"""M1 S1.3 端到端验证: 模拟前端点 Run, 验证 SkillsPage 路径通"""
import json
import urllib.request


def get(path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:8080{path}", timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path: str, body: dict):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:8080{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


print("=" * 60)
print("M1 S1.3 SkillsPage E2E")
print("=" * 60)

# 1) MCP 健康 (页面顶部条)
mcp = get("/api/v1/skills/mcp/health")
print(f"\n[1] MCP health: {mcp}")
assert mcp["all_ok"], f"MCP not ok: {mcp}"

# 2) Skill 列表 (页面 ag-grid)
skills = get("/api/v1/skills")["items"]
print(f"\n[2] Skills registered ({len(skills)}):")
for s in skills:
    print(f"    - {s['name']}  v{s['version']}  agent={s['agent']}")
assert len(skills) >= 2, "Need at least 2 skills"

# 3) Run discover (模拟"打开 drawer → Run")
print("\n[3] Run: discover_clickhouse_assets")
disc = post("/api/v1/skills/run", {"name": "discover_clickhouse_assets", "inputs": {"database": "shop"}})
assert disc["ok"], f"discover failed: {disc}"
s = disc["output"]["summary"]
print(f"    ok={disc['ok']}  duration={disc['duration_ms']}ms  tables={s['tables_total']}  updated={s['updated']}")

# 4) Run infer (模拟在 drawer 改 inputs → Run)
print("\n[4] Run: infer_table_description")
inf = post("/api/v1/skills/run", {"name": "infer_table_description", "inputs": {"sample_rows": 2}})
assert inf["ok"], f"infer failed: {inf}"
s = inf["output"]["summary"]
print(f"    ok={inf['ok']}  duration={inf['duration_ms']}ms  summary={s}")

# 5) 检查资产 (跨页签一致性)
print("\n[5] GET /api/v1/assets (资产页应看到)")
assets = get("/api/v1/assets?limit=10")
print(f"    total assets: {assets['total']}")
assert assets["total"] >= 6, f"expected >= 6 assets, got {assets['total']}"

# 6) 检查建议
print("\n[6] GET /api/v1/suggestions?status=pending (建议页应看到)")
sug = get("/api/v1/suggestions?status=pending&limit=10")
print(f"    pending suggestions: {sug['total']}")
assert sug["total"] >= 6, f"expected >= 6 pending, got {sug['total']}"

print("\n" + "=" * 60)
print("M1 S1.3 E2E PASS")
print("=" * 60)
