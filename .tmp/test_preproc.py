"""Test dbt Jinja preprocessor."""
import re


def preprocess_dbt_sql(sql: str, jinja_resolve: dict[str, str] | None = None) -> str:
    """把 dbt 模板 ({{ ref('x') }}, {{ source('x','y') }}, {% ... %}) 替换成纯 SQL.

    jinja_resolve: 可选 { ref('x') -> actual_table_name, source('x','y') -> actual_table_name }
    默认就用 ref/source 的第一个参数作为表名 (skip multi-arg)

    返回预处理后的 SQL. 保留所有普通 SQL 语法.
    """
    if not sql:
        return sql
    jinja_resolve = jinja_resolve or {}
    # 1) 处理 {{ ref('x') }} {{ ref("x") }} → x
    def _ref(m):
        key = f"ref({m.group(1)!r})"  # ref('x') 形式
        if key in jinja_resolve:
            return jinja_resolve[key]
        return m.group(1)
    sql = re.sub(r"\{\{\s*ref\(\s*['\"]?(\w+)['\"]?\s*\)\s*\}\}", _ref, sql)
    # 2) 处理 {{ source('x', 'y') }} → y (或 jinja_resolve)
    def _source(m):
        schema_name, table_name = m.group(1), m.group(2)
        key = f"source({schema_name!r}, {table_name!r})"
        if key in jinja_resolve:
            return jinja_resolve[key]
        return table_name
    sql = re.sub(
        r"\{\{\s*source\(\s*['\"]?(\w+)['\"]?\s*,\s*['\"]?(\w+)['\"]?\s*\)\s*\}\}",
        _source,
        sql,
    )
    # 3) 处理 {{ this }} → 自身 (在 SQL 中无意义, 直接替换为占位)
    sql = re.sub(r"\{\{\s*this\s*\}\}", "_this_", sql)
    # 4) 处理 {{ var('x') }} → 'x' (literal)
    sql = re.sub(r"\{\{\s*var\(\s*['\"]?(\w+)['\"]?\s*\)\s*\}\}", r"'\1'", sql)
    # 5) 删除 {% ... %} 块 (if/else/for) — 简化处理, 仅删条件
    # 对简单的 {% if X %} ... {% endif %} 块: 只保留内部
    # 对简单的 {% for x in Y %} ... {% endfor %} 块: 只保留内部 (循环体)
    # 简化: 删除所有的 {% ... %} tags, 保留 block 内部
    sql = re.sub(r"\{%\s*endfor\s*%\}", "", sql)
    sql = re.sub(r"\{%\s*endif\s*%\}", "", sql)
    sql = re.sub(r"\{%\s*endmacro\s*%\}", "", sql)
    sql = re.sub(r"\{%\s*for\s+\w+\s+in\s+[^%]+?%\}", "", sql)  # 只删起始 tag
    sql = re.sub(r"\{%\s*if\s+[^%]+?%\}", "", sql)  # 只删起始 tag
    sql = re.sub(r"\{%\s*else\s*%\}", "", sql)
    # 6) 删除 {{ config(...) }} 块 (一般多行)
    sql = re.sub(r"\{\{\s*config\([^)]*\)\s*\}\}", "", sql, flags=re.DOTALL)
    # 7) 清理多余空行
    sql = re.sub(r"\n\s*\n+", "\n\n", sql)
    return sql.strip()


# Test
DIM_USERS_RAW = """{{ config(materialized='table') }}

WITH first_orders AS (
  SELECT user_id, MIN(created_at) AS first_order_at
  FROM {{ ref('stg_orders') }}
  WHERE status = 'paid'
  GROUP BY user_id
)
SELECT
  u.id          AS user_id,
  u.email       AS email,
  u.phone       AS phone,
  cs.name_zh    AS country,
  fo.first_order_at
FROM {{ source('raw', 'users') }} u
LEFT JOIN first_orders fo ON fo.user_id = u.id
LEFT JOIN {{ ref('country_seed') }} cs ON cs.code = u.country_code
"""

FCT_RAW = """{{ config(materialized='incremental', unique_key='order_date_user_id') }}

SELECT
  toDate(o.created_at)         AS order_date,
  o.user_id                    AS user_id,
  countDistinct(o.id)          AS order_count,
  sum(o.amount)                AS gmv
FROM {{ ref('stg_orders') }} o
{% if is_incremental() %}
WHERE o.created_at > (SELECT max(order_date) FROM {{ this }})
{% endif %}
GROUP BY order_date, o.user_id
"""

print("=== dim_users ===")
print(preprocess_dbt_sql(DIM_USERS_RAW))
print("\n=== fct_orders_daily ===")
print(preprocess_dbt_sql(FCT_RAW))
