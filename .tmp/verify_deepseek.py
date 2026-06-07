"""M1 S1.4 smoke: 用 deepseek 跑 infer, 验证 suggestion.model != 'mock'."""
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
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


print("=" * 60)
print("DeepSeek LLM smoke")
print("=" * 60)

# 1) MCP
mcp = get("/api/v1/skills/mcp/health")
print(f"\n[1] MCP: {mcp}")
assert mcp["all_ok"]

# 2) 跑 infer (profile=default → gpt-5 跳过无 key → deepseek)
print("\n[2] Run infer_table_description (default profile → expect deepseek)")
resp = post(
    "/api/v1/skills/run",
    {"name": "infer_table_description", "inputs": {"sample_rows": 1, "min_confidence": 0.0}},
)
if not resp["ok"]:
    print(f"   FAIL: {resp.get('error')}")
    raise SystemExit(1)
s = resp["output"]["summary"]
items = resp["output"]["items"]
print(f"   ok={resp['ok']}  duration={resp['duration_ms']}ms")
print(f"   summary: {s}")
print(f"   items[0]: {items[0] if items else '(empty)'}")

# 3) 校验 model 字段
if items:
    model = items[0].get("model", "")
    print(f"\n[3] model used: '{model}'")
    assert "mock" not in model, f"still got mock: {model}"
    assert "deepseek" in model, f"expected deepseek, got: {model}"
    print(f"    OK: using real deepseek model")

# 4) 校验 description 不再带 [mock-no-key] 前缀
sug = get("/api/v1/suggestions?status=pending&limit=20")
print(f"\n[4] pending suggestions: {sug['total']}")
print("   last 3 payloads (description):")
for it in sug["items"][-3:]:
    pl = it.get("payload", {})
    desc = pl.get("description", "")[:80]
    print(f"     - {it['model']:30s} | {desc}")

print("\n" + "=" * 60)
print("DeepSeek smoke PASS")
print("=" * 60)
