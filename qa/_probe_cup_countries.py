"""Which COUNTRY does each cup-ish competition carry, and what does the backend say?

Run:  backend\\.venv\\Scripts\\python.exe qa/_probe_cup_countries.py
"""
import sqlite3
import sys

sys.path.insert(0, "backend")
from app import flashscore as fs  # noqa: E402

c = sqlite3.connect("backend/goaledge.db")
cur = c.cursor()
rows = cur.execute(
    """
    SELECT co.name AS country, comp.name, comp.slug
    FROM competitions comp
    LEFT JOIN countries co ON co.id = comp.country_id
    WHERE comp.name LIKE '%hampions%' OR comp.name LIKE '%uropa%'
    ORDER BY co.name, comp.name
    """
).fetchall()
print("country / competition / slug  ->  backend is_top_league")
for country, name, slug in rows:
    print(f"  {country!r:<12} {name!r:<52} {slug:<52} -> {fs.is_top_league(country, name)}")

print()
print("CompetitionOut shape (first few, with country):")
for r in cur.execute(
    """
    SELECT co.name, comp.name, comp.slug
    FROM competitions comp LEFT JOIN countries co ON co.id = comp.country_id
    ORDER BY comp.name LIMIT 6
    """
).fetchall():
    print("  ", r)
