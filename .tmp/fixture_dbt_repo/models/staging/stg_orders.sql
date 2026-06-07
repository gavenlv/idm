{{ config(materialized='view', tags=['staging']) }}
select id, user_id, amount, status, created_at
from {{ source('raw', 'orders') }}
