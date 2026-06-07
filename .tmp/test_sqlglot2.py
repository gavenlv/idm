import sqlglot
sql = 'SELECT order_id, total_amount FROM `clickhouse-prod.shop.default.orders_daily` ORDER BY created_at DESC LIMIT 3'
try:
    p = sqlglot.parse(sql, read='clickhouse')
    print('OK', p[0])
    for t in p[0].find_all(sqlglot.exp.Table):
        print('TBL:', repr(t.name), repr(t.args.get('db')), repr(t.args.get('catalog')))
except Exception as e:
    print('FAIL', e)
