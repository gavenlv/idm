-- @description: 用户维表
{{ config(materialized='table', tags=['pii', 'core']) }}
with users as (
  select id, email, phone, created_at from {{ source('raw', 'users') }}
)
select id::String as user_id,  -- 用户 id
       email::String as email,  -- 邮箱
       phone::String as phone,  -- 手机
       created_at::DateTime as created_at
from users
