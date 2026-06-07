"""M1 S1.7 验证: analyze_dbt_code Skill (local_path mode)."""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8080"
API = BASE + "/api/v1"
FIXTURE_DIR = r"d:\workspace\github-ai\idm\.tmp\fixture_dbt_repo"


def req(path, method="GET", body=None, params=None):
    url = API + path
    if params:
        from urllib.parse import urlencode
        url += "?" + urlencode(params)
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def get(p, **kw):
    return req(p, "GET", params=kw)


def post(p, body):
    return req(p, "POST", body)


def check(cond, label):
    if cond:
        print(f"    [OK] {label}")
    else:
        print(f"    [FAIL] {label}")
        raise SystemExit(1)


def main():
    print("=" * 60)
    print("M1 S1.7 验证: analyze_dbt_code Skill (local mode)")
    print("=" * 60)

    # 0) 健康检查
    code, h = get("/skills")
    check(code == 200, f"GET /skills (200)")
    skill_names = [s["name"] for s in h.get("items", [])]
    check("analyze_dbt_code" in skill_names, f"analyze_dbt_code registered")

    # 1) dry-run: 仅解析, 不写库
    print("\n[1] dry-run: 解析 3 个 .sql")
    code, r = post(
        "/skills/run",
        {
            "name": "analyze_dbt_code",
            "inputs": {
                "local_path": FIXTURE_DIR,
                "project_name": "shop_dw",
                "project_label": "shop",
                "base_path": "models",
            },
            "dry_run": True,
        },
    )
    check(code == 200 and r.get("ok"), f"dry-run ok (got ok={r.get('ok')}, err={r.get('error')})")
    s = (r.get("output", {}).get("summary", {}) or r.get("summary", {}) or {})
    print(f"    summary: {json.dumps({k: s[k] for k in ('sql_files_total', 'files_scanned', 'refs_found', 'sources_found', 'descriptions_extracted', 'tags_extracted') if k in s}, ensure_ascii=False)}")
    check(s.get("sql_files_total", 0) >= 3, f"sql_files_total >=3 (got {s.get('sql_files_total')})")
    check(s.get("refs_found", 0) >= 2, f"refs_found >=2 (got {s.get('refs_found')})")
    check(s.get("sources_found", 0) >= 2, f"sources_found >=2 (got {s.get('sources_found')})")
    check(s.get("descriptions_extracted", 0) >= 2, f"descriptions_extracted >=2 (got {s.get('descriptions_extracted')})")
    check(s.get("tags_extracted", 0) >= 3, f"tags_extracted >=3 (got {s.get('tags_extracted')})")

    items = r.get("output", {}).get("items", [])
    fqns = [it["fqn"] for it in items]
    print(f"    FQNs: {fqns}")
    check("dbt-shop_dw.shop.default.dim_users" in fqns, f"dim_users in fqns")
    check("dbt-shop_dw.shop.staging.stg_orders" in fqns, f"stg_orders in fqns")
    check("dbt-shop_dw.shop.default.fct_orders_daily" in fqns, f"fct_orders_daily in fqns")

    # 2) 真跑: 写入 KG (需先有 dbt manifest 跑过的 TableAsset)
    print("\n[2] 真跑: 写入 KG")
    code, r2 = post(
        "/skills/run",
        {
            "name": "analyze_dbt_code",
            "inputs": {
                "local_path": FIXTURE_DIR,
                "project_name": "shop_dw",
                "project_label": "shop",
                "base_path": "models",
            },
            "dry_run": False,
        },
    )
    check(code == 200 and r2.get("ok"), f"real run ok (got ok={r2.get('ok')}, err={r2.get('error')})")
    s2 = (r2.get("output", {}).get("summary", {}) or r2.get("summary", {}) or {})
    print(f"    summary: {json.dumps({k: s2[k] for k in ('tables_enriched', 'columns_described', 'refs_found', 'sources_found') if k in s2}, ensure_ascii=False)}")
    check(s2.get("tables_enriched", 0) >= 3, f"tables_enriched >=3 (got {s2.get('tables_enriched')})")
    # columns_described 可能是 0 (manifest 已有描述) 或 >0, 都算通过
    cd = s2.get("columns_described", 0)
    print(f"    columns_described: {cd} (manifest 可能已含描述)")

    # 3) 验证: 写库后查询 dim_users, 应有 dbt_refs / dbt_sources / code_refs
    print("\n[3] 验证: dim_users 强化结果")
    code, a = get("/assets", service="dbt-shop_dw", search="dim_users", limit=5)
    dim = next((x for x in a.get("items", []) if x.get("name") == "dim_users"), None)
    check(dim is not None, f"dim_users found")
    if dim:
        code, det = get(f"/assets/{dim['id']}")
        extra = det.get("extra", {}) or {}
        print(f"    extra keys: {list(extra.keys())}")
        print(f"    dbt_refs: {extra.get('dbt_refs')}")
        print(f"    dbt_sources: {extra.get('dbt_sources')}")
        print(f"    dbt_tags: {extra.get('dbt_tags')}")
        print(f"    code_refs count: {len(extra.get('code_refs', []))}")
        print(f"    description: {det.get('description')}")
        check(len(extra.get("dbt_sources", [])) >= 1, f"dbt_sources >=1")
        check(len(extra.get("code_refs", [])) >= 1, f"code_refs >=1")
        check(det.get("description") is not None, f"description present")
        # 列描述 (从 /columns 端点查)
        code, cdata = get(f"/assets/{dim['id']}/columns")
        print(f"    /columns code: {code}, items: {len(cdata.get('items', []))}")
        cols = cdata.get("items", [])
        for c in cols:
            if c["name"] in ("email", "user_id") and c.get("description"):
                print(f"    col[{c['name']}] desc: {c['description']}")
        email_col = next((c for c in cols if c["name"] == "email"), None)
        print(f"    email_col: {email_col is not None}, has_desc: {bool(email_col and email_col.get('description'))}")
        check(email_col is not None and email_col.get("description"), f"email column has description")

    print("\n" + "=" * 60)
    print("M1 S1.7 PASS")
    print("=" * 60)


if __name__ == "__main__":
    main()
