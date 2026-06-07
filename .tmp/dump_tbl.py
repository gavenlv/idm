import psycopg
conn = psycopg.connect('postgresql://idm:idm@localhost:5432/idm')
cur = conn.cursor()
cur.execute("SELECT id, fqn, name FROM table_assets WHERE fqn LIKE 'clickhouse%' LIMIT 5")
for r in cur.fetchall():
    print(r)
