import sqlite3
c = sqlite3.connect('backend/goaledge.db')
cur = c.cursor()
print("Search 'league phase' / 'champions':")
rows = cur.execute("""
    SELECT comp.name, comp.slug, co.name AS country
    FROM competitions comp
    LEFT JOIN countries co ON co.id = comp.country_id
    WHERE comp.name LIKE '%hampions League%'
    ORDER BY co.name, comp.name
""").fetchall()
for r in rows:
    print("  ", r)

print()
print("Fixtures per these Europe comps (this-season-ish, kickoff >= 2025-07-01):")
rows = cur.execute("""
    SELECT co.name, comp.name, comp.slug, COUNT(*)
    FROM fixtures f
    JOIN competitions comp ON comp.id = f.competition_id
    LEFT JOIN countries co ON co.id = comp.country_id
    WHERE f.kickoff >= '2025-07-01'
      AND (comp.name LIKE '%hampions League%' OR comp.name LIKE '%uropa%')
    GROUP BY comp.id
    ORDER BY COUNT(*) DESC
""").fetchall()
for r in rows:
    print("  ", r)

print()
print("Top-league comps in DB:")
for key_c in [('England','Premier League'),('Spain','LaLiga'),('Italy','Serie A'),('Germany','Bundesliga'),('France','Ligue 1'),('Netherlands','Eredivisie')]:
    r = cur.execute("""
        SELECT comp.name, comp.slug, COUNT(f.id)
        FROM competitions comp
        LEFT JOIN countries co ON co.id = comp.country_id
        LEFT JOIN fixtures f ON f.competition_id = comp.id AND f.kickoff >= '2025-07-01'
        WHERE co.name=? AND comp.name=?
        GROUP BY comp.id
    """, key_c).fetchall()
    print("  ", key_c, "->", r)
