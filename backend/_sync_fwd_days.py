# Forward-window day sync only (fast: no per-fixture history probing).
# Run from the backend folder:  .\.venv\Scripts\python.exe _sync_fwd_days.py

import sys

sys.path.insert(0, ".")

from app.database import SessionLocal
import app.sync as fixture_sync

db = SessionLocal()
try:
    for offset in (1, 2, 3):
        result = fixture_sync.sync_day(db, offset)
        print("forward sync", result.as_dict(), flush=True)
finally:
    db.close()
