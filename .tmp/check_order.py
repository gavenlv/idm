"""验证: 最新 3 条 pending 是 deepseek 生成的, 老的是 mock."""
import json
import urllib.request


def get(path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:8080{path}", timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


sug = get("/api/v1/suggestions?status=pending&limit=20")
print(f"pending total: {sug['total']}")
print("\n[最新 3 条 by created_at]")
sorted_items = sorted(sug["items"], key=lambda x: x["created_at"], reverse=True)[:3]
for it in sorted_items:
    pl = it.get("payload", {})
    desc = pl.get("description", "")[:100]
    print(f"  {it['model']:30s}  conf={it['confidence']:.2f}  | {desc}")

print("\n[最老 3 条 by created_at]")
sorted_items_old = sorted(sug["items"], key=lambda x: x["created_at"])[:3]
for it in sorted_items_old:
    pl = it.get("payload", {})
    desc = pl.get("description", "")[:60]
    print(f"  {it['model']:30s}  conf={it['confidence']:.2f}  | {desc}")
