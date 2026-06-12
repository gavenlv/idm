"""Comprehensive tests for infer_column_lineage SQL parsing.

覆盖:
  1) basic column passthrough (u.id AS user_id)
  2) CAST (id::String AS id)
  3) dbt-style alias 还原 (u -> users, cs -> country_seed)
  4) JOIN (LEFT JOIN ... ON ...)
  5) 聚合函数 (SUM, COUNT, MIN, MAX, AVG, countDistinct)
  6) 窗口函数 (ROW_NUMBER() OVER (...))
  7) CASE WHEN / If
  8) 算术运算 (price * quantity)
  9) CTE 内部列 (不应追溯到 CTE 来源表)
 10) 通用函数 (toDate, year, ...)
"""
import sys
sys.path.insert(0, "apps/api/src")
sys.path.insert(0, "packages/kg/src")

from idm_api.skills.builtin.infer_column_lineage import _parse_sql_with_sqlglot

CASES = [
    # 1. basic
    {
        "name": "basic passthrough",
        "sql": "SELECT u.id AS user_id, u.email AS email FROM users u",
        "upstream": ["dbt.users"],
        "expect": [
            ("dbt.users", "id", "user_id"),
            ("dbt.users", "email", "email"),
        ],
    },
    # 2. CAST
    {
        "name": "CAST ::String",
        "sql": "SELECT u.id::String AS user_id FROM users u",
        "upstream": ["dbt.users"],
        "expect": [
            ("dbt.users", "id", "user_id"),
        ],
    },
    # 3. alias resolution
    {
        "name": "dbt alias cs -> country_seed",
        "sql": "SELECT cs.name_zh AS country FROM country_seed cs",
        "upstream": ["dbt.country_seed"],
        "expect": [
            ("dbt.country_seed", "name_zh", "country"),
        ],
    },
    # 4. JOIN
    {
        "name": "JOIN with ON",
        "sql": (
            "SELECT u.id AS user_id, cs.name_zh AS country "
            "FROM users u LEFT JOIN country_seed cs ON cs.code = u.country_code"
        ),
        "upstream": ["dbt.users", "dbt.country_seed"],
        "expect": [
            ("dbt.users", "id", "user_id"),
            ("dbt.country_seed", "name_zh", "country"),
        ],
    },
    # 5. aggregates
    {
        "name": "aggregates sum + countDistinct",
        "sql": (
            "SELECT "
            "  toDate(o.created_at) AS order_date, "
            "  o.user_id AS user_id, "
            "  countDistinct(o.id) AS order_count, "
            "  sum(o.amount) AS gmv "
            "FROM stg_orders o "
            "GROUP BY order_date, o.user_id"
        ),
        "upstream": ["dbt.stg_orders"],
        "expect_min": 4,
        "transforms": {"aggregation", "function", "direct"},
    },
    # 6. window
    {
        "name": "window function ROW_NUMBER() OVER",
        "sql": (
            "SELECT u.id AS user_id, "
            "ROW_NUMBER() OVER (PARTITION BY u.country ORDER BY u.created_at) AS rn "
            "FROM users u"
        ),
        "upstream": ["dbt.users"],
        "expect_min": 3,
        "transforms": {"window", "direct"},
    },
    # 7. CASE WHEN
    {
        "name": "CASE WHEN derivation",
        "sql": (
            "SELECT u.id AS user_id, "
            "CASE WHEN u.amount > 100 THEN 'big' ELSE 'small' END AS order_size "
            "FROM orders u"
        ),
        "upstream": ["dbt.orders"],
        "expect_min": 2,
        "transforms": {"derivation", "direct"},
    },
    # 8. arithmetic
    {
        "name": "arithmetic price * quantity",
        "sql": (
            "SELECT u.id AS user_id, u.price * u.quantity AS total FROM orders u"
        ),
        "upstream": ["dbt.orders"],
        "expect_min": 3,
        "transforms": {"arithmetic", "direct"},
    },
    # 9. CTE - first_order_at is from CTE (no upstream), should NOT be matched
    {
        "name": "CTE columns not traced to source table",
        "sql": (
            "WITH first_orders AS ("
            "  SELECT user_id, MIN(created_at) AS first_order_at "
            "  FROM stg_orders WHERE status = 'paid' GROUP BY user_id"
            ") "
            "SELECT "
            "  u.id AS user_id, "
            "  u.email AS email, "
            "  fo.first_order_at "
            "FROM users u "
            "LEFT JOIN first_orders fo ON fo.user_id = u.id"
        ),
        "upstream": ["dbt.users", "dbt.stg_orders"],
        # 应该有 user_id, email 边 (来自 users), first_order_at 不会被错误归到 stg_orders
        "expect_no": [
            ("dbt.stg_orders", "first_order_at"),
        ],
    },
    # 10. generic function
    {
        "name": "generic function toDate",
        "sql": "SELECT toDate(u.created_at) AS order_date FROM users u",
        "upstream": ["dbt.users"],
        "expect_min": 1,
        "transforms": {"function"},
    },
]


def main():
    passed = 0
    failed = 0
    for tc in CASES:
        name = tc["name"]
        edges = _parse_sql_with_sqlglot(tc["sql"], tc["upstream"], "downstream")
        actual = [(e["upstream_fqn"], e["upstream_col"], e["downstream_col"]) for e in edges]
        transforms = {e["transform_type"] for e in edges}
        ok = True
        msgs = []
        if "expect" in tc:
            for exp in tc["expect"]:
                if exp not in actual:
                    ok = False
                    msgs.append(f"  MISSING: {exp}")
        if "expect_no" in tc:
            for fqn, col in tc["expect_no"]:
                if any(e["upstream_fqn"] == fqn and e["upstream_col"] == col for e in edges):
                    ok = False
                    msgs.append(f"  UNEXPECTED: {fqn}.{col}")
        if "expect_min" in tc and len(edges) < tc["expect_min"]:
            ok = False
            msgs.append(f"  TOO_FEW: got {len(edges)}, expected >= {tc['expect_min']}")
        if "transforms" in tc:
            missing = tc["transforms"] - transforms
            if missing:
                ok = False
                msgs.append(f"  MISSING_TRANSFORM: {missing}")
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"[{status}] {name} ({len(edges)} edges, transforms={transforms})")
        for msg in msgs:
            print(msg)
        if not ok:
            for e in edges:
                print(f"    {e['upstream_fqn']}.{e['upstream_col']} -> {e['downstream_col']} ({e['transform_type']})")

    print(f"\n=== Total: {passed} passed, {failed} failed ===")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
