# Sync a forward window too: the Predictions page lists *upcoming* fixtures, so
# pulling only today + lookback leaves tomorrow empty.
# Run from the backend folder:  .\.venv\Scripts\python.exe _sync_forward.py

import sys

sys.path.insert(0, ".")

from app.database import SessionLocal
import app.sync as fixture_sync

# The forward window is now part of the normal refresh (app.refresh.DailyRefresher
# calls sync_forward, then prices, then warms the prediction cache), so this
# script just triggers that path directly instead of duplicating the order here.
db = SessionLocal()
try:
    for result in fixture_sync.sync_forward(db, days=7):
        print("forward sync", result.as_dict())

    history = fixture_sync.import_team_history(db, limit=60)
    print("history", history)

    priced = fixture_sync.price_flashscore_fixtures(db, limit=3000)
    print("priced", priced)

    warmed = fixture_sync.warm_prediction_cache(db, days=3)
    print("warmed", warmed)
finally:
    db.close()
