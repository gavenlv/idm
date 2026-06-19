-- @description: 用户维表
{{ config(materialized='table', tags=['pii', 'core']) }}
with first_orders as (
  select user_id, min(created_at) as first_order_at
  from {{ ref('stg_orders') }}
  where status = 'paid'
  group by user_id
),
users as (
  select id, email, phone, country_code, created_at from {{ source('raw', 'users') }}
)
select u.id::String as user_id,  -- 用户 id
       u.email::String as email,  -- 邮箱
       u.phone::String as phone,  -- 手机
       cs.name_zh as country,  -- 国家 (来自 country_seed 维表)
       fo.first_order_at  -- 首单时间 (来自 CTE, 无列血缘)
from users u
left join first_orders fo on fo.user_id = u.id
left join {{ ref('country_seed') }} cs on cs.code = u.country_code
