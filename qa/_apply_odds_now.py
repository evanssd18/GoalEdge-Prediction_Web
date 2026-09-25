"""One-shot: overlay Odds API prices onto the local Flashscore fixtures."""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.database import SessionLocal  # noqa: E402
from app.oddsapi import apply_book_odds  # noqa: E402
from app.pricing import is_model_priced  # noqa: E402
from app.models import Fixture  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

db = SessionLocal()
try:
    result = apply_book_odds(db, force=True)
    print(json.dumps(result, default=str, indent=2))
    rows = db.execute(
        select(Fixture.bookmaker, func.count())
        .where(Fixture.source != "seed", Fixture.status.in_(["scheduled", "live"]))
        .group_by(Fixture.bookmaker)
    ).all()
    print("upcoming bookmakers:")
    for book, n in rows:
        kind = "model" if is_model_priced(book) else "book"
        print(f"  {n:4d}  {book or '(none)'}  [{kind}]")
finally:
    db.close()
