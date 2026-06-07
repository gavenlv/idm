"""临时验证脚本: 拉资产列表 + 列服务."""
import json
import urllib.request

# 1) 列资产
req = urllib.request.Request("http://127.0.0.1:8080/api/v1/assets?page=1&size=20")
with urllib.request.urlopen(req, timeout=5) as r:
    d = json.loads(r.read().decode("utf-8"))

print(f"=== Assets in KG (total={d.get('total', '?')}) ===")
for a in d["items"]:
    print(f"  {a['fqn']:55s}  cols={a.get('column_count', 0):2d}  rows={a.get('row_count')}")

# 2) 列服务
req = urllib.request.Request("http://127.0.0.1:8080/api/v1/services")
with urllib.request.urlopen(req, timeout=5) as r:
    s = json.loads(r.read().decode("utf-8"))
print(f"\n=== Services (total={len(s)}) ===")
for svc in s:
    conn = svc.get("connection", {})
    print(f"  {svc['name']:30s}  type={svc.get('service_type')}  host={conn.get('host')}:{conn.get('port')}")

