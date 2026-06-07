"""E2E 验证: assets + skills + suggestions + approve."""
import json
import urllib.request

def get(path):
    with urllib.request.urlopen(f"http://127.0.0.1:8080{path}", timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))

def post(path, body=None):
    data = json.dumps(body).encode("utf-8") if body else b""
    req = urllib.request.Request(
        f"http://127.0.0.1:8080{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))

# 1. Health
h = get("/health/ready")
print(f"API ready: db={h.get('checks', {}).get('database')}")

# 2. MCP health
m = get("/api/v1/skills/mcp/health")
print(f"MCP: {m}")

# 3. Skills registry
sk = get("/api/v1/skills")
print(f"Skills: {[s['name'] for s in sk['items']]}")

# 4. Services
svcs = get("/api/v1/services")
print(f"Services: {len(svcs)} -> {[s['name'] for s in svcs]}")

# 5. Assets
a = get("/api/v1/assets?page=1&size=20")
print(f"Assets: {a.get('total', '?')} tables")
for it in a["items"]:
    print(f"  - {it['fqn']:55s}  cols={it.get('column_count')}  rows={it.get('row_count')}")

# 6. Suggestions
sg = get("/api/v1/suggestions")
print(f"Suggestions: total={sg.get('total', '?')}, items={len(sg.get('items', []))}")
for s in sg.get("items", [])[:5]:
    p = s.get("payload", {})
    desc = p.get("description", "") if isinstance(p, dict) else str(p)[:80]
    print(f"  [{s['status']}] {s['suggestion_type']} conf={s.get('confidence')}  -> {desc[:70]}")

# 7. 审核闭环: approve 一条
if sg.get("items"):
    sid = sg["items"][0]["id"]
    print(f"\n>>> Approve suggestion {sid[:8]} ...")
    try:
        result = post(f"/api/v1/suggestions/{sid}/approve", body={"reviewer": "e2e-test", "note": "ok"})
        print(f"  approve ok: {result.get('ok')}, status={result.get('status')}")
    except urllib.error.HTTPError as e:
        print(f"  approve failed: {e.code} {e.read().decode()[:200]}")

