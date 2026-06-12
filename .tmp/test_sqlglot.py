"""Test sqlglot parsing of dbt SQL."""
import re

try:
    import sqlglot
    from sqlglot import exp
    print(f"sqlglot version: {sqlglot.__version__}")
except ImportError:
    print("sqlglot not installed")
    raise

# Simulate the dim_users dbt SQL with jinja preprocessed
DIM_USERS_SQL = """
WITH first_orders AS (
  SELECT user_id, MIN(created_at) AS first_order_at
  FROM stg_orders
  WHERE status = 'paid'
  GROUP BY user_id
)
SELECT
  u.id          AS user_id,
  u.email       AS email,
  u.phone       AS phone,
  cs.name_zh    AS country,
  fo.first_order_at
FROM users u
LEFT JOIN first_orders fo ON fo.user_id = u.id
LEFT JOIN country_seed cs ON cs.code = u.country_code
"""

FCT_ORDERS_SQL = """
SELECT
  toDate(o.created_at)         AS order_date,
  o.user_id                    AS user_id,
  countDistinct(o.id)          AS order_count,
  sum(o.amount)                AS gmv
FROM stg_orders o
GROUP BY order_date, o.user_id
"""

STG_ORDERS_SQL = """
SELECT
  id        AS order_id,
  user_id   AS user_id,
  amount    AS amount,
  status    AS status
FROM orders
"""

def parse(sql, downstream_fqn, upstream_fqns):
    for dialect in ("", "clickhouse", "hive", "spark", "postgres"):
        try:
            ast = sqlglot.parse_one(sql, read=dialect or None)
            if ast:
                print(f"  parsed with dialect={dialect or 'default'}")
                break
        except Exception as e:
            ast = None
    if ast is None:
        print("  FAILED to parse")
        return
    if not isinstance(ast, exp.Select):
        print(f"  not a SELECT, got {type(ast).__name__}")
        return
    print(f"  downstream: {downstream_fqn}")
    print(f"  upstream candidates: {upstream_fqns}")
    for proj in ast.expressions:
        col_name = proj.alias_or_name
        # Find source columns
        sources = []
        for node in proj.find_all(exp.Column):
            sources.append(f"{node.table}.{node.name}" if node.table else node.name)
        print(f"    {col_name} <- {sources}")


print("=== dim_users ===")
parse(DIM_USERS_SQL, "dbt-shop_dw.shop.default.dim_users",
      ["dbt-shop_dw.shop.raw.users", "dbt-shop_dw.shop.staging.stg_orders", "dbt-shop_dw.shop.default.country_seed"])

print("\n=== fct_orders_daily ===")
parse(FCT_ORDERS_SQL, "dbt-shop_dw.shop.default.fct_orders_daily",
      ["dbt-shop_dw.shop.staging.stg_orders"])

print("\n=== stg_orders ===")
parse(STG_ORDERS_SQL, "dbt-shop_dw.shop.staging.stg_orders",
      ["dbt-shop_dw.shop.raw.orders"])
