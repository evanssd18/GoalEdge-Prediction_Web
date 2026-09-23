"""Throwaway: verify the import-order assumption design-check.py relies on."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

t = tempfile.NamedTemporaryFile(prefix="dbg-", suffix=".db", delete=False)
t.close()
os.environ["DATABASE_URL"] = "sqlite:///" + t.name.replace(os.sep, "/")
os.environ["FLASHSCORE_ENABLED"] = "false"

from app import design  # noqa: E402
from app import config  # noqa: E402
from app.database import engine  # noqa: E402

print("temp file      =", t.name)
print("settings URL   =", config.settings.database_url)
print("engine URL     =", engine.url)
print("flashscore     =", config.settings.flashscore_enabled)
os.unlink(t.name)