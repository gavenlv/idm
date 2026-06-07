"""M1 S1.4 E2E: PII skill with deepseek."""
import json
import urllib.request


def get(path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:8080{path}", timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path: str, body: dict, timeout: int = 60):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:8080{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


print("=" * 60)
print("M1 S1.4 PII E2E")
print("=" * 60)

# 1) 列出所有 skills, 验证 PII 已注册
skills = get("/api/v1/skills")["items"]
print(f"\n[1] Skills registered ({len(skills)}):")
for s in skills:
    print(f"    - {s['name']:30s}  v{s['version']}  agent={s['agent']}")
assert any(s["name"] == "classify_pii_columns" for s in skills), "PII skill not registered"

# 2) 先 discover 确保有列资产
print("\n[2] Ensure assets exist (run discover first)")
disc = post(
    "/api/v1/skills/run",
    {"name": "discover_clickhouse_assets", "inputs": {"database": "shop"}},
)
print(f"    discover: ok={disc['ok']} tables={disc['output']['summary']['tables_total']}")

# 3) Run PII classification
print("\n[3] Run classify_pii_columns")
resp = post(
    "/api/v1/skills/run",
    {"name": "classify_pii_columns", "inputs": {"min_confidence": 0.0, "limit": 50}},
    timeout=180,
)
if not resp["ok"]:
    print(f"    FAIL: {resp.get('error')}")
    raise SystemExit(1)
s = resp["output"]["summary"]
items = resp["output"]["items"]
print(f"    ok={resp['ok']}  duration={resp['duration_ms']}ms")
print(f"    summary: {json.dumps(s, ensure_ascii=False)}")
print(f"    items: {len(items)}")

# 4) 验证不是 mock
if items:
    model = items[0].get("model", "")
    print(f"\n[4] model used: '{model}'")
    assert "mock" not in model, f"still got mock: {model}"

# 5) 看分类分布
print("\n[5] Classification distribution:")
by_class: dict[str, int] = {}
for it in items:
    by_class[it["pii_class"]] = by_class.get(it["pii_class"], 0) + 1
for cls, n in sorted(by_class.items(), key=lambda x: -x[1]):
    print(f"    {cls:15s}  {n}")

# 6) 抽查 PII 推断质量 (email/phone 应有命中)
print("\n[6] Sample high-PII detections:")
detected = [it for it in items if it["pii_class"] not in ("none", "uuid_pseudo")][:5]
for it in detected:
    print(f"    {it['table_fqn']:55s}  {it['column_name']:20s}  → {it['pii_class']:12s}  ({it['confidence']:.2f})  mask={it['masking_policy']}")

# 7) 验证 ai_suggestion 落了 pii_class 类型
print("\n[7] Check ai_suggestion for pii_class entries")
sug = get("/api/v1/suggestions?status=pending&suggestion_type=pii_class&limit=5")
print(f"    pending pii_class suggestions: {sug['total']}")
if sug["items"]:
    it = sug["items"][0]
    print(f"    sample: type={it['suggestion_type']} target_type={it['target_type']} model={it['model']}")
    print(f"            payload={json.dumps(it['payload'], ensure_ascii=False)}")

print("\n" + "=" * 60)
print("M1 S1.4 PII E2E PASS")
print("=" * 60)
