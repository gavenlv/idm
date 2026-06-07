"""verify_s1_11.py: M1 S1.11 验证 (Anomaly / Insight 引擎 + 周期任务)."""
import json
import sys

import httpx
import psycopg

BASE = "http://127.0.0.1:8080/api/v1"
TIMEOUT = 60.0
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


def check(c: bool, label: str) -> None:
    if c:
        print(f"    [OK] {label}")
    else:
        print(f"    [FAIL] {label}")
        failed.append(label)


print("=" * 60)
print("M1 S1.11 验证: Anomaly / Insight 引擎")
print("=" * 60)

# 1) Skill 注册
code, sk = get("/skills")
check(code == 200, "GET /skills (200)")
names = [s["name"] for s in sk.get("items", [])]
check("detect_anomalies" in names, "detect_anomalies registered")

# 2) Dry-run: skip_drift=true (mock CH 没 history) 跑 owner_gap + pii_escalation
print("\n[1] detect_anomalies dry-run (owner_gap + pii_escalation)")
code, r = post("/skills/run", {
    "name": "detect_anomalies",
    "inputs": {
        "service": "clickhouse-prod",
        "limit": 10,
        "apply": False,
        "skip_drift": True,
        "skip_null": True,
    },
})
check(code == 200, f"POST /skills/run ({code})")
check(r.get("ok") is True, f"ok=True (got {r.get('ok')}, err={r.get('error')})")
summary = r.get("output", {}).get("summary", {})
print(f"    summary: {summary}")
check(summary.get("tables_scanned", 0) > 0, f"tables_scanned > 0 (got {summary.get('tables_scanned')})")
findings = r.get("output", {}).get("items", [])
print(f"    findings: {len(findings)}")
for f in findings[:5]:
    print(f"      - {f['table_fqn']} :: {f['kind']} ({f['severity']})")
check(len(findings) >= 1, f"at least 1 finding (got {len(findings)})")
# 应有 owner_gap
kinds = {f["kind"] for f in findings}
check("owner_gap" in kinds, f"owner_gap detected (kinds: {kinds})")

# 3) Apply: 写入 ai_suggestion
print("\n[2] detect_anomalies apply=True (写 ai_suggestion)")
code, r2 = post("/skills/run", {
    "name": "detect_anomalies",
    "inputs": {
        "service": "clickhouse-prod",
        "limit": 5,
        "apply": True,
        "skip_drift": True,
        "skip_null": True,
    },
})
check(code == 200, f"apply run ({code})")
check(r2.get("ok") is True, f"apply ok=True (got {r2.get('ok')}, err={r2.get('error')})")
n = r2.get("output", {}).get("summary", {}).get("findings", 0)
print(f"    findings: {n}")

# 4) 校验: ai_suggestion 表有 pending 行
conn = psycopg.connect("postgresql://idm:idm@localhost:5432/idm")
cur = conn.cursor()
cur.execute(
    """
    SELECT suggestion_type, status, count(*)
    FROM ai_suggestions
    WHERE skill = 'detect_anomalies' AND created_at > now() - INTERVAL '5 minute'
    GROUP BY 1,2
    """
)
rows = cur.fetchall()
print(f"    ai_suggestions: {rows}")
check(len(rows) >= 1, f"ai_suggestion rows written (got {len(rows)} groups)")

# 5) 仅 owner_gap 检测 (skip 其他)
print("\n[3] detect_anomalies 仅 owner_gap")
code, r3 = post("/skills/run", {
    "name": "detect_anomalies",
    "inputs": {
        "service": "clickhouse-prod",
        "limit": 3,
        "apply": False,
        "skip_drift": True,
        "skip_null": True,
        "skip_pii": True,
    },
})
check(code == 200, f"owner_gap-only ({code})")
findings3 = r3.get("output", {}).get("items", [])
kinds3 = {f["kind"] for f in findings3}
print(f"    findings: {len(findings3)} (kinds: {kinds3})")
check(kinds3 == {"owner_gap"} or kinds3 == set(), f"only owner_gap (got {kinds3})")

# 6) suggestion 至少 1 条含 suggested_action 字段
if rows:
    cur.execute(
        """SELECT payload FROM ai_suggestions WHERE skill='detect_anomalies' ORDER BY created_at DESC LIMIT 1"""
    )
    p = cur.fetchone()[0]
    check(isinstance(p, dict) and "details" in p, f"suggestion payload ok: {p}")
cur.close()
conn.close()

print("\n" + "=" * 60)
if failed:
    print(f"M1 S1.11 FAIL: {len(failed)} checks failed")
    for f in failed:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("M1 S1.11 PASS")
