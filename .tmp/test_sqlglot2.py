"""Test sqlglot alias → table name resolution."""
import sqlglot
from sqlglot import exp

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


def resolve_aliases(sql):
    """Return dict: alias → table name (or cte name)"""
    ast = sqlglot.parse_one(sql)
    alias_map = {}
    for tbl in ast.find_all(exp.Table):
        # Get table alias (e.g. `users u` → u, `country_seed cs` → cs)
        alias = tbl.alias
        name = tbl.name
        if alias:
            alias_map[alias] = name
        else:
            alias_map[name] = name
    return alias_map


def parse_with_aliases(sql, downstream_fqn, upstream_fqns):
    ast = sqlglot.parse_one(sql)
    alias_map = resolve_aliases(sql)
    print(f"  alias map: {alias_map}")
    up_short_names = {f.split(".")[-1]: f for f in upstream_fqns}
    print(f"  upstream by short: {up_short_names}")
    print(f"  downstream: {downstream_fqn}")
    for proj in ast.expressions:
        col_name = proj.alias_or_name
        sources = []
        for node in proj.find_all(exp.Column):
            alias_or_table = node.table
            if alias_or_table in alias_map:
                tbl_name = alias_map[alias_or_table]
                # check if this is an upstream
                up_fqn = up_short_names.get(tbl_name)
                if up_fqn:
                    sources.append(f"{tbl_name}.{node.name}  → {up_fqn}")
                else:
                    sources.append(f"{tbl_name}.{node.name}  (cte/local)")
            else:
                sources.append(f"{alias_or_table or '?'}.{node.name}")
        print(f"    {col_name} <- {sources}")


print("=== dim_users ===")
parse_with_aliases(DIM_USERS_SQL, "dbt-shop_dw.shop.default.dim_users",
      ["dbt-shop_dw.shop.raw.users", "dbt-shop_dw.shop.staging.stg_orders", "dbt-shop_dw.shop.default.country_seed"])

print("\n=== fct_orders_daily ===")
parse_with_aliases(FCT_ORDERS_SQL, "dbt-shop_dw.shop.default.fct_orders_daily",
      ["dbt-shop_dw.shop.staging.stg_orders"])
