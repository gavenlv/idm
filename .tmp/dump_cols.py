import psycopg
conn = psycopg.connect('postgresql://idm:idm@localhost:5432/idm')
cur = conn.cursor()
cur.execute("""SELECT t.fqn, c.name, c.data_type, c.description FROM table_assets t
               JOIN column_assets c ON c.table_id=t.id
               WHERE t.fqn LIKE 'clickhouse%' LIMIT 30""")
for r in cur.fetchall():
    print(repr(r))
