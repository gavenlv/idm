-- @description: 订单日聚合事实表
{{ config(materialized='incremental', tags=['core']) }}
select
  d.user_id as user_id,
  o.order_date as order_date,
  count(o.id) as order_count,
  sum(o.amount) as gmv
from {{ ref('stg_orders') }} o
join {{ ref('dim_users') }} d on o.user_id = d.user_id
group by d.user_id, o.order_date
