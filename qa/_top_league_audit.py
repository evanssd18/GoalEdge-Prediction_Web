"""Which competitions does the TOP_LEAGUE_KEYS test actually accept?

Run:  backend\\.venv\\Scripts\\python.exe qa\\_top_league_audit.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.flashscore import TOP_LEAGUE_KEYS, is_top_league  # noqa: E402

DB = ROOT / "backend" / "goaledge.db"
con = sqlite3.connect(DB)

print("TOP_LEAGUE_KEYS:")
for k in TOP_LEAGUE_KEYS:
    print("   ", k)

print("\n== competitions matching a top-league name, and the verdict ==")
rows = con.execute(
    """
    SELECT c.id, c.name, c.slug, co.name, COUNT(f.id)
    FROM competitions c
    LEFT JOIN countries co ON co.id = c.country_id
    LEFT JOIN fixtures f ON f.competition_id = c.id
    WHERE LOWER(c.slug) LIKE '%premier%' OR LOWER(c.slug) LIKE '%laliga%'
       OR LOWER(c.slug) LIKE '%serie-a%' OR LOWER(c.slug) LIKE '%bundesliga%'
       OR LOWER(c.slug) LIKE '%ligue-1%' OR LOWER(c.slug) LIKE '%eredivisie%'
       OR LOWER(c.slug) LIKE '%champions-league%' OR LOWER(c.slug) LIKE '%europa-league%'
    GROUP BY c.id
    ORDER BY 5 DESC
    """
).fetchall()
for r in rows:
    verdict = "TOP" if is_top_league(r[3], r[1]) else "---"
    print(f"  {verdict:<4} id={r[0]:<5} n={r[4]:<5} country={r[3]!r:<18} name={r[1]!r:<42} slug={r[2]}")

print("\n== the Europe-country competitions, all of them ==")
rows = con.execute(
    """
    SELECT c.id, c.name, c.slug, co.name, COUNT(f.id)
    FROM competitions c
    LEFT JOIN countries co ON co.id = c.country_id
    LEFT JOIN fixtures f ON f.competition_id = c.id
    WHERE co.name = 'Europe'
    GROUP BY c.id
    ORDER BY 5 DESC
    """
).fetchall()
for r in rows:
    verdict = "TOP" if is_top_league(r[3], r[1]) else "---"
    print(f"  {verdict:<4} id={r[0]:<5} n={r[4]:<5} name={r[1]!r:<48} slug={r[2]}")

con.close()
