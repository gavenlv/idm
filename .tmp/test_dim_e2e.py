"""End-to-end test: dbt SQL preprocessing + column lineage parsing.

Verifies the full pipeline for dim_users (design doc 80% coverage):
  1) _preprocess_dbt_sql: strip Jinja templates ({{ ref() }}, {{ source() }}, {% if %})
  2) _parse_sql_with_sqlglot: parse preprocessed SQL → column lineage edges
  3) Verify 4/5 columns have lineage (80% coverage), first_order_at has no lineage
"""
import sys
sys.path.insert(0, "apps/api/src")
sys.path.insert(0, "packages/kg/src")

from idm_api.skills.builtin.parse_dbt_manifest import _preprocess_dbt_sql
from idm_api.skills.builtin.infer_column_lineage import _parse_sql_with_sqlglot

# Raw dbt SQL from fixture_dbt_manifest.json (with Jinja templates)
DIM_USERS_RAW = (
    "{{ config(materialized='table') }}\n\n"
    "WITH first_orders AS (\n"
    "  SELECT user_id, MIN(created_at) AS first_order_at\n"
    "  FROM {{ ref('stg_orders') }}\n"
    "  WHERE status = 'paid'\n"
    "  GROUP BY user_id\n"
    ")\n"
    "SELECT\n"
    "  u.id          AS user_id,\n"
    "  u.email       AS email,\n"
    "  u.phone       AS phone,\n"
    "  cs.name_zh    AS country,\n"
    "  fo.first_order_at\n"
    "FROM {{ source('raw', 'users') }} u\n"
    "LEFT JOIN first_orders fo ON fo.user_id = u.id\n"
    "LEFT JOIN {{ ref('country_seed') }} cs ON cs.code = u.country_code\n"
)

# fct_orders_daily with {% if is_incremental() %} block
FCT_ORDERS_RAW = (
    "{{ config(materialized='incremental', unique_key='order_date_user_id') }}\n\n"
    "SELECT\n"
    "  toDate(o.created_at)         AS order_date,\n"
    "  o.user_id                    AS user_id,\n"
    "  countDistinct(o.id)          AS order_count,\n"
    "  sum(o.amount)                AS gmv\n"
    "FROM {{ ref('stg_orders') }} o\n"
    "{% if is_incremental() %}\n"
    "WHERE o.created_at > (SELECT max(order_date) FROM {{ this }})\n"
    "{% endif %}\n"
    "GROUP BY order_date, o.user_id\n"
)


def test_dim_users():
    """dim_users: 4/5 columns have lineage (80% coverage)."""
    print("=== dim_users ===")
    preprocessed = _preprocess_dbt_sql(DIM_USERS_RAW)
    print(f"preprocessed SQL:\n{preprocessed}\n")

    upstream = [
        "dbt-shop_dw.shop.raw.users",
        "dbt-shop_dw.shop.staging.stg_orders",
        "dbt-shop_dw.shop.default.country_seed",
    ]
    edges = _parse_sql_with_sqlglot(preprocessed, upstream, "dbt-shop_dw.shop.default.dim_users")
    print(f"edges: {len(edges)}")
    for e in edges:
        print(f"  {e['upstream_fqn']}.{e['upstream_col']} -> {e['downstream_col']} ({e['transform_type']})")

    # Expected: 4 edges (user_id, email, phone, country)
    assert len(edges) == 4, f"Expected 4 edges, got {len(edges)}"

    # Verify each expected edge
    edge_set = {(e["upstream_fqn"].split(".")[-1], e["upstream_col"], e["downstream_col"]) for e in edges}
    expected = {
        ("users", "id", "user_id"),
        ("users", "email", "email"),
        ("users", "phone", "phone"),
        ("country_seed", "name_zh", "country"),
    }
    missing = expected - edge_set
    assert not missing, f"Missing edges: {missing}"

    # Verify first_order_at is NOT matched to stg_orders (CTE handling)
    for e in edges:
        assert e["downstream_col"] != "first_order_at", \
            f"first_order_at should have no lineage, but got: {e}"

    print("PASS: dim_users 4/5 columns have lineage (80% coverage)\n")


def test_fct_orders_daily():
    """fct_orders_daily: aggregation + function transforms."""
    print("=== fct_orders_daily ===")
    preprocessed = _preprocess_dbt_sql(FCT_ORDERS_RAW)
    print(f"preprocessed SQL:\n{preprocessed}\n")

    upstream = ["dbt-shop_dw.shop.staging.stg_orders"]
    edges = _parse_sql_with_sqlglot(preprocessed, upstream, "dbt-shop_dw.shop.default.fct_orders_daily")
    print(f"edges: {len(edges)}")
    for e in edges:
        print(f"  {e['upstream_fqn']}.{e['upstream_col']} -> {e['downstream_col']} ({e['transform_type']})")

    # Expected: 4 edges (order_date, user_id, order_count, gmv)
    assert len(edges) == 4, f"Expected 4 edges, got {len(edges)}"

    transforms = {e["transform_type"] for e in edges}
    assert "aggregation" in transforms, f"Expected aggregation transform, got: {transforms}"
    assert "function" in transforms, f"Expected function transform (toDate), got: {transforms}"
    assert "direct" in transforms, f"Expected direct transform (user_id), got: {transforms}"

    print("PASS: fct_orders_daily aggregation + function transforms\n")


if __name__ == "__main__":
    test_dim_users()
    test_fct_orders_daily()
    print("=== All end-to-end tests passed ===")
