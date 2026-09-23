"""Backfill team.logo on an already-seeded database.

Seeding is idempotent and skips when fixtures exist, so an existing DB keeps its
old placeholder logos. This rewrites them in place without touching fixtures,
results or predictions.

Run from repo root:   python qa/backfill_logos.py
Dry run (no writes):  python qa/backfill_logos.py --dry-run
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.crests import crest_for_slug, FALLBACK_LOGO  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Team  # noqa: E402

dry = "--dry-run" in sys.argv

db = SessionLocal()
teams = db.scalars(select(Team).order_by(Team.id)).all()

real = 0
fallback = 0
changed = 0
missing: list[str] = []

for team in teams:
    url = crest_for_slug(team.slug)
    if url:
        real += 1
    else:
        url = FALLBACK_LOGO
        fallback += 1
        missing.append(f"{team.slug} ({team.name})")
    if team.logo != url:
        changed += 1
        if not dry:
            team.logo = url

if not dry:
    db.commit()

print(f"teams            : {len(teams)}")
print(f"real badge       : {real}")
print(f"fallback glyph   : {fallback}")
print(f"rows changed     : {changed}{'  (dry run, nothing written)' if dry else ''}")
if missing:
    print("\nno badge for:")
    for m in missing:
        print("  -", m)
db.close()
