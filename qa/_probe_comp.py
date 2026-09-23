import sqlite3
c = sqlite3.connect('backend/goaledge.db')
cur = c.cursor()
print("TABLES:", [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()])
rows = cur.execute("""
    SELECT comp.name, comp.slug, co.name AS country
    FROM competitions comp
    LEFT JOIN countries co ON co.id = comp.country_id
    WHERE comp.name LIKE '%hampions%' OR comp.name LIKE '%uropa%'
       OR co.name = 'Europe'
    ORDER BY co.name, comp.name
""").fetchall()
print("EUROPE/CL/EL COMPETITIONS:", len(rows))
for r in rows:
    print("  ", r)

print()
print("FIXTURE STATUS COUNTS:")
for r in cur.execute("SELECT status, COUNT(*) FROM fixtures GROUP BY status").fetchall():
    print("  ", r)
print()
print("DATE RANGE:", cur.execute("SELECT MIN(kickoff), MAX(kickoff) FROM fixtures").fetchone())

