"""Inspect which competitions exist and how many fixtures each holds.

Run:  backend\\.venv\\Scripts\\python.exe qa\\_db_overview.py
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "backend" / "goaledge.db"
con = sqlite3.connect(DB)
cur = con.cursor()

print("== competitions: cups + top leagues ==")
rows = cur.execute(
    """
    SELECT c.id, c.name, c.slug, COUNT(f.id) AS n,
           SUM(CASE WHEN f.source = 'flashscore' THEN 1 ELSE 0 END) AS real_n
    FROM competitions c
    LEFT JOIN fixtures f ON f.competition_id = c.id
    GROUP BY c.id
    ORDER BY n DESC
    LIMIT 40
    """
).fetchall()
for r in rows:
    print(f"  id={r[0]:<5} n={r[3]:<6} real={r[4]:<6} {r[1]}  [{r[2]}]")

print("\n== cup-named competitions ==")
rows = cur.execute(
    """
    SELECT c.id, c.name, c.slug, COUNT(f.id)
    FROM competitions c
    LEFT JOIN fixtures f ON f.competition_id = c.id
    WHERE LOWER(c.name) LIKE '%champions%'
       OR LOWER(c.name) LIKE '%europa%'
       OR LOWER(c.name) LIKE '%conference%'
       OR LOWER(c.name) LIKE '%cup%'
    GROUP BY c.id
    ORDER BY 4 DESC
    LIMIT 40
    """
).fetchall()
for r in rows:
    print(f"  id={r[0]:<5} n={r[3]:<6} {r[1]}  [{r[2]}]")

print("\n== fixture date range ==")
print(cur.execute("SELECT MIN(kickoff), MAX(kickoff), COUNT(*) FROM fixtures").fetchone())

print("\n== by source ==")
for r in cur.execute("SELECT source, COUNT(*) FROM fixtures GROUP BY source").fetchall():
    print(f"  {r[0]}: {r[1]}")

print("\n== how many competitions total ==")
print(cur.execute("SELECT COUNT(*) FROM competitions").fetchone()[0])
con.close()
