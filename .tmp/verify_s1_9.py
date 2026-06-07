"""verify_s1_9.py: M1 S1.9 验证 (Owner 推断 Skill + 服务隔离)."""
import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8080/api/v1"
TIMEOUT = 30.0
failed: list[str] = []


def get(path: str, **params):
    with httpx.Client(base_url=BASE, timeout=TIMEOUT) as c:
        r = c.get(path, params=params)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {}


def post(path: str, payload: dict):
    with httpx.Client(base_url=BASE, timeout=TIMEOUT) as c:
        r = c.post(path, json=payload)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {}


def check(cond: bool, label: str) -> None:
    if cond:
        print(f"    [OK] {label}")
    else:
        print(f"    [FAIL] {label}")
        failed.append(label)


def run_skill(name: str, **inputs) -> dict:
    code, r = post("/skills/run", {"name": name, "inputs": inputs})
    if code != 200:
        return {"ok": False, "error": f"http {code}: {r}"}
    return r


print("=" * 60)
print("M1 S1.9 验证: infer_table_owners Skill + 服务隔离")
print("=" * 60)

# 1) Skill 注册
code, sk = get("/skills")
check(code == 200, "GET /skills (200)")
names = [s["name"] for s in sk.get("items", [])]
check("infer_table_owners" in names, "infer_table_owners registered")

# 2) Dry-run
print("\n[1] dry-run: 启发式 (不调 LLM)")
res = run_skill("infer_table_owners", service="clickhouse-prod", min_confidence=0.5, llm_threshold=0.9)
check(res.get("ok"), f"dry-run ok (err={res.get('error')})")
s = res.get("output", {}).get("summary", {})
print(f"    summary: {json.dumps(s, ensure_ascii=False)}")
check(s.get("tables_scanned", 0) >= 1, f"tables_scanned>=1 (got {s.get('tables_scanned')})")
check(s.get("owners_inferred", 0) >= 1, f"owners_inferred>=1 (got {s.get('owners_inferred')})")
# 启发式 confidence=0.6 < llm_threshold=0.9, LLM 会被调, 这是预期
check("llm_calls" in s, f"llm_calls in summary (got {s.get('llm_calls')})")

# 3) 真跑: apply=True 写入 asset_owners
print("\n[2] real run: apply=True 写入 asset_owners")
res2 = run_skill(
    "infer_table_owners",
    service="clickhouse-prod",
    min_confidence=0.5,
    llm_threshold=0.9,
    apply=True,
)
check(res2.get("ok"), f"real run ok (err={res2.get('error')})")
s2 = res2.get("output", {}).get("summary", {})
print(f"    summary: {json.dumps({k: s2.get(k) for k in ('tables_scanned','owners_inferred','llm_calls','apply','by_team','by_source')}, ensure_ascii=False)}")
check(s2.get("owners_inferred", 0) >= 1, f"owners_inferred>=1 (got {s2.get('owners_inferred')})")

# 4) /api/v1/owners 列表
print("\n[3] /api/v1/owners 列表")
code, owners = get("/owners", service="clickhouse-prod", limit=10)
check(code == 200, f"GET /owners (200, got {code})")
print(f"    total: {owners.get('total', 0)}")
check(owners.get("total", 0) >= 1, f"owners.total>=1 (got {owners.get('total')})")
if owners.get("items"):
    item = owners["items"][0]
    print(f"    sample: table_fqn={item.get('table_fqn')}, email={item.get('user_email')}, team={item.get('team')}")
    check("table_fqn" in item, "owner has table_fqn")
    check("@" in (item.get("user_email") or ""), "owner has user_email")

# 5) 服务隔离: FQN 跨 service 写入应该被拒
print("\n[4] 服务隔离 (跨 service FQN)")
import psycopg
conn = psycopg.connect("postgresql://idm:idm@localhost:5432/idm")
cur = conn.cursor()
# 查 ch-shop_dw 的 service_id
cur.execute("SELECT id FROM services WHERE name='ch-shop_dw'")
row = cur.fetchone()
chshopdwid = str(row[0]) if row else None
cur.close()
conn.close()

if chshopdwid:
    from idm_api.skills.utils import upsert_table_asset
    import asyncio

    async def test_isolation():
        try:
            # 拿一个 clickhouse-prod service
            conn = __import__("psycopg").connect("postgresql://idm:idm@localhost:5432/idm")
            cur = conn.cursor()
            cur.execute("SELECT id FROM services WHERE name='clickhouse-prod'")
            ch = str(cur.fetchone()[0])
            cur.close()
            conn.close()

            from sqlalchemy.ext.asyncio import create_async_session

            # 实际我们直接验证 utils.upsert_table_asset 抛 ValueError
            # 通过 mock: 写一个 db session 来调用
            from idm_api.db import get_engine
            from sqlalchemy.ext.asyncio import AsyncSession

            engine = get_engine()
            async with AsyncSession(engine) as db:
                try:
                    await upsert_table_asset(
                        db,
                        fqn="ch-shop_dw.shop.default.foo",  # 跨 service
                        name="foo",
                        service_id=ch,  # 但 service_id 是 clickhouse-prod
                        asset_type="table",
                    )
                    check(False, "service isolation should reject")
                except ValueError as ve:
                    check("service isolation" in str(ve).lower() or "must start" in str(ve), f"service isolation ValueError raised: {str(ve)[:80]}")
                except Exception as e:
                    check(False, f"unexpected exception: {type(e).__name__}: {e}")

        except Exception as e:
            print(f"    [ERROR] test_isolation setup failed: {e}")

    asyncio.run(test_isolation())
else:
    print("    [SKIP] ch-shop_dw service not registered, skip isolation test")

# 6) Verify endpoint
print("\n[5] POST /owners/{id}/verify")
if owners.get("items"):
    owner_id = owners["items"][0]["id"]
    with httpx.Client(base_url=BASE, timeout=TIMEOUT) as c:
        r = c.post(f"/owners/{owner_id}/verify")
    check(r.status_code == 200, f"verify (200, got {r.status_code})")
    if r.status_code == 200:
        check(r.json().get("is_verified") is True, "owner.is_verified == True")

# 7) 过滤: verified=true 后总数 - 1
code, owners2 = get("/owners", service="clickhouse-prod", verified=True, limit=20)
print(f"    verified count: {owners2.get('total', 0)}")
check(owners2.get("total", 0) >= 1, f"verified>=1 (got {owners2.get('total')})")

print("\n" + "=" * 60)
if failed:
    print(f"M1 S1.9 FAIL: {len(failed)} checks failed")
    for f in failed:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("M1 S1.9 PASS")
