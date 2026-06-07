import sqlglot
for q in [
    'SELECT 1 FROM `clickhouse-prod.shop.default.orders_daily` LIMIT 3',
    'SELECT 1 FROM "clickhouse-prod.shop.default.orders_daily" LIMIT 3',
    'SELECT 1 FROM clickhouse_prod.shop.default.orders_daily LIMIT 3',
]:
    try:
        p = sqlglot.parse(q, read='clickhouse')
        print('OK', q[:60])
    except Exception as e:
        print('FAIL', e)
