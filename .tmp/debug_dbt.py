import urllib.request, json
body = json.dumps({"inputs": {"manifest_path": r"d:\workspace\github-ai\idm\.tmp\fixture_dbt_manifest.json", "project_name": "shop_dw", "write_lineage": True}}).encode()
r = urllib.request.Request("http://127.0.0.1:8080/api/v1/skills/parse_dbt_manifest/run", data=body, method="POST", headers={"Content-Type": "application/json"})
resp = urllib.request.urlopen(r, timeout=60).read().decode()
print(resp[:3000])
