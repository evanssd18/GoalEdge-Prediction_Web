# One-off operator script: sync the Flashscore feed, then make the real
# fixtures first-class predictions (team ratings history + model prices).
#
# Run from the backend folder:  .\.venv\Scripts\python.exe _run_sync.py

import sys

sys.path.insert(0, ".")

from app.database import SessionLocal
from app.sync import (
    import_team_history,
    price_flashscore_fixtures,
    source_counts,
    sync_recent,
)

db = SessionLocal()
try:
    outcomes = sync_recent(db, days=2, history=False)
    for o in outcomes:
        print("sync", o.as_dict())

    history = import_team_history(db, limit=60)
    print("history", history)

    priced = price_flashscore_fixtures(db, limit=1000)
    print("priced", priced)

    print("source_counts", source_counts(db))
finally:
    db.close()
