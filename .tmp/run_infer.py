"""临时验证脚本: 跑 infer_table_description Skill, 验证 ai_suggestion."""
import json
import urllib.request

# 1) 拿到 table_ids
req = urllib.request.Request("http://127.0.0.1:8080/api/v1/assets?page=1&size=20")
with urllib.request.urlopen(req, timeout=5) as r:
    d = json.loads(r.read().decode("utf-8"))
table_ids = [a["id"] for a in d["items"]]
print(f"Got {len(table_ids)} table_ids to infer")

# 2) 跑 infer
body = json.dumps({
    "name": "infer_table_description",
    "inputs": {"table_ids": table_ids, "sample_rows": 2},
}).encode("utf-8")
req = urllib.request.Request(
    "http://127.0.0.1:8080/api/v1/skills/run",
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=120) as r:
    out = json.loads(r.read().decode("utf-8"))

print(f"\n=== infer_table_description result (ok={out.get('ok')}, dur={out.get('duration_ms')}ms) ===")
print(json.dumps(out.get("output", {}), ensure_ascii=False, indent=2))
if out.get("error"):
    print("ERROR:", out["error"])

# 3) 查建议列表
req = urllib.request.Request("http://127.0.0.1:8080/api/v1/suggestions?status=pending")
with urllib.request.urlopen(req, timeout=5) as r:
    sg = json.loads(r.read().decode("utf-8"))
print(f"\n=== Pending suggestions: {len(sg)} ===")
for s in sg[:3]:
    print(f"  [{s['suggestion_type']}] {s.get('entity_id', '?')[:8]}  -> {s.get('payload', {}).get('description', '?')[:80]}")
