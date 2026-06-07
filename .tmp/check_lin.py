import psycopg
c = psycopg.connect("postgresql://idm:idm@localhost:5432/idm")
cur = c.cursor()
cur.execute("SELECT upstream_id, downstream_id, transform_type, source FROM table_lineage")
rows = cur.fetchall()
for r in rows:
    print(r)
print("total:", len(rows))

cur.execute("SELECT id, fqn FROM table_assets WHERE fqn LIKE 'superset.%' ORDER BY fqn")
for r in cur.fetchall():
    print("asset:", r)
c.close()
