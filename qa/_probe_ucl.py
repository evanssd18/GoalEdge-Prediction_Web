import sqlite3
c = sqlite3.connect('backend/goaledge.db')
cur = c.cursor()
print("All competitions whose slug contains 'champions-league' or 'europa-league':")
for r in cur.execute("""
    SELECT co.name, comp.name, comp.slug,
           (SELECT COUNT(*) FROM fixtures f WHERE f.competition_id=comp.id) AS fx
    FROM competitions comp LEFT JOIN countries co ON co.id=comp.country_id
    WHERE comp.slug LIKE '%champions-league%' OR comp.slug LIKE '%europa-league%'
    ORDER BY co.name, comp.name
""").fetchall():
    print("  ", r)

print()
print("Fixtures with kickoff this-season-ish for those comps:")
for r in cur.execute("""
    SELECT co.name, comp.name, comp.slug, COUNT(*) AS n,
           MIN(f.kickoff), MAX(f.kickoff)
    FROM fixtures f JOIN competitions comp ON comp.id=f.competition_id
    LEFT JOIN countries co ON co.id=comp.country_id
    WHERE comp.slug LIKE '%champions-league%' OR comp.slug LIKE '%europa-league%'
    GROUP BY comp.id ORDER BY n DESC
""").fetchall():
    print("  ", r)
