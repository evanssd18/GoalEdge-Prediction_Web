# Report the state of real (Flashscore) fixtures so a sync's effect is visible.
# Run from the backend folder:  .\.venv\Scripts\python.exe _report.py

import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Fixture
from app.sync import source_counts

db = SessionLocal()
try:
    print("source_counts", source_counts(db))
    now = datetime.now(timezone.utc)

    for label, delta in (("today", 0), ("tomorrow", 1), ("yesterday", -1)):
        day = (now + timedelta(days=delta)).date()
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        rows = db.scalars(
            select(Fixture)
            .where(Fixture.kickoff >= start, Fixture.kickoff < end)
            .order_by(Fixture.kickoff)
        ).all()
        real = [r for r in rows if r.source == "flashscore"]
        live = [r for r in real if r.status == "live"]
        priced = [r for r in real if r.odds_home is not None]
        upcoming = [r for r in real if r.status in ("scheduled", "live")]
        print(
            f"{label:9} {day}  total={len(rows):4d} real={len(real):4d} "
            f"live={len(live):3d} upcoming={len(upcoming):4d} priced={len(priced):4d}"
        )

    # A sample of the next real fixtures from now, so the board content is
    # concrete rather than only counted.
    upcoming = db.scalars(
        select(Fixture)
        .where(Fixture.source == "flashscore", Fixture.status.in_(["scheduled", "live"]))
        .order_by(Fixture.kickoff)
        .limit(15)
    ).all()
    print("\nnext real fixtures:")
    for fx in upcoming:
        comp = fx.competition
        print(
            f"  {fx.kickoff.strftime('%Y-%m-%d %H:%M')}Z {fx.status:9} "
            f"{fx.home_team.short_name} v {fx.away_team.short_name}  "
            f"[{comp.country.name if comp.country else '-'} / {comp.name}] "
            f"odds={'yes' if fx.odds_home else 'no'}"
        )
finally:
    db.close()
