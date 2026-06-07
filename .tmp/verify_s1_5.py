"""M1 S1.5 E2E: Asset 详情 + PII 摘要 + dbt manifest Skill.

测试:
  A) GET /api/v1/assets/{id}/columns       — 列清单
  B) GET /api/v1/assets/{id}/pii-summary   — PII 摘要
  C) run parse_dbt_manifest (fixture)      — dbt manifest → KG
  D) 验证 dbt 资产可被 /assets 看到, 列清单可查
"""
import json
import os
import urllib.request


def get(path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:8080{path}", timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path: str, body: dict | None = None, timeout: int = 60):
    data = json.dumps(body).encode("utf-8") if body is not None else b""
    req = urllib.request.Request(
        f"http://127.0.0.1:8080{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


print("=" * 60)
print("M1 S1.5 E2E: Asset 详情 + PII 摘要 + dbt Skill")
print("=" * 60)

# A) 找一张表 (users, 应已被 PII skill 处理过)
assets = get("/api/v1/assets?limit=200")["items"]
users_tbl = next((a for a in assets if a["fqn"].endswith("users") and "clickhouse" in a["fqn"]), None)
assert users_tbl, "users table not found; run discover first"
print(f"\n[A] target table: {users_tbl['fqn']} (id={users_tbl['id'][:8]}...)")

# 先 approve 所有 users 表的 pending pii_class 建议, 让 column_asset 拿到 PII
print(f"\n[A.0] approve all pending pii suggestions for users columns")
users_cols = get(f"/api/v1/assets/{users_tbl['id']}/columns")
col_ids = {c["id"] for c in users_cols["items"]}
all_pii_sug = get("/api/v1/suggestions?status=pending&suggestion_type=pii_class&limit=200")
approved = 0
for s in all_pii_sug["items"]:
    if s["target_id"] in col_ids:
        post(f"/api/v1/suggestions/{s['id']}/approve", {"note": "e2e-setup"}, timeout=15)
        approved += 1
print(f"    approved: {approved}")

cols = get(f"/api/v1/assets/{users_tbl['id']}/columns")
print(f"    columns: {cols['total']}")
assert cols["total"] > 0
pii_cols = [c for c in cols["items"] if c["pii_class"] != "none"]
print(f"    pii columns: {len(pii_cols)}")
for c in pii_cols:
    print(f"      {c['name']:20s}  → {c['pii_class']:10s}  conf={c['pii_confidence']:.2f}")
assert len(pii_cols) >= 3, f"expected at least 3 PII cols in users, got {len(pii_cols)}"

# B) PII summary
pii_sum = get(f"/api/v1/assets/{users_tbl['id']}/pii-summary")
print(f"\n[B] pii-summary:")
print(f"    pii_columns: {pii_sum['pii_columns']}")
print(f"    high_risk_columns: {pii_sum['high_risk_columns']}")
print(f"    by_class: {pii_sum['by_class']}")
assert pii_sum["high_risk_columns"] >= 2, "users should have at least 2 high-risk PII cols"

# C) dbt manifest skill
fixture = os.path.abspath("d:/workspace/github-ai/idm/.tmp/fixture_dbt_manifest.json")
print(f"\n[C] Run parse_dbt_manifest ({fixture})")
resp = post(
    "/api/v1/skills/run",
    {"name": "parse_dbt_manifest", "inputs": {"manifest_path": fixture, "project_name": "shop_dw"}},
    timeout=60,
)
if not resp["ok"]:
    print(f"    FAIL: {resp.get('error')}")
    raise SystemExit(1)

s = resp["output"]["summary"]
print(f"    ok={resp['ok']}  duration={resp['duration_ms']}ms")
print(f"    project: {s['project']}  service: {s['service']}")
print(f"    by_resource_type: {s['by_resource_type']}")
print(f"    tables_created: {s['tables_created']}  updated: {s['tables_updated']}")
print(f"    total_depends_on_edges: {s['total_depends_on_edges']}")

# D) 验证 dbt 表入 KG
dbt_assets = get("/api/v1/assets?service=dbt-shop_dw&limit=200")["items"]
print(f"\n[D] dbt assets in KG: {len(dbt_assets)}")
for a in dbt_assets:
    print(f"    {a['fqn']:50s}  type={a['asset_type']:10s}  desc='{a['description'][:50] if a['description'] else '—'}'")
assert len(dbt_assets) >= 4, f"expected >=4 dbt assets, got {len(dbt_assets)}"

# E) 看 dbt 模型的列 (dim_users 应有 5 列, 含 PII 提示)
dim_users = next((a for a in dbt_assets if a["fqn"].endswith("dim_users")), None)
if dim_users:
    dbt_cols = get(f"/api/v1/assets/{dim_users['id']}/columns")
    print(f"\n[E] dim_users columns: {dbt_cols['total']}")
    pii_in_dbt = [c for c in dbt_cols["items"] if c["pii_class"] != "none"]
    print(f"    pii flagged (粗筛): {len(pii_in_dbt)}")
    for c in dbt_cols["items"]:
        print(f"      {c['name']:20s}  type={c['data_type']:15s}  pii={c['pii_class']:10s}  desc='{(c['description'] or '—')[:30]}'")

# F) 重复跑应全部 updated (幂等)
print("\n[F] Re-run (idempotency test)")
resp2 = post(
    "/api/v1/skills/run",
    {"name": "parse_dbt_manifest", "inputs": {"manifest_path": fixture, "project_name": "shop_dw"}},
    timeout=60,
)
s2 = resp2["output"]["summary"]
print(f"    run #2: created={s2['tables_created']}  updated={s2['tables_updated']}")
assert s2["tables_created"] == 0, f"expected 0 created on re-run, got {s2['tables_created']}"

# G) 验证 dry_run
print("\n[G] dry_run test")
resp3 = post(
    "/api/v1/skills/run",
    {"name": "parse_dbt_manifest", "inputs": {"manifest_path": fixture, "project_name": "shop_dw", "dry_run": True}},
    timeout=60,
)
s3 = resp3["output"]["summary"]
print(f"    dry_run items: {s3['items_total']}  created: {s3['tables_created']} (should be 0)")
assert s3["tables_created"] == 0

print("\n" + "=" * 60)
print("M1 S1.5 E2E PASS")
print("=" * 60)
