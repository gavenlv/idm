import psycopg
c = psycopg.connect("postgresql://idm:idm@localhost:5432/idm")
cur = c.cursor()
cur.execute("DELETE FROM table_assets WHERE fqn IN ('clickhouse-prod.shop.default.payments', 'clickhouse-prod.shop.default.products')")
print("deleted rows:", cur.rowcount)
c.commit()
c.close()
