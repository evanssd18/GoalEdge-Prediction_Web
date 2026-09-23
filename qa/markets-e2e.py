"""End-to-end check of the market category index, without needing a live server.

Runs the real FastAPI app in-process via TestClient against the SportyBet mock,
so it exercises the actual route, taxonomy and payload -- no shell, no port
juggling, no environment guesswork.

Run from repo root:  python qa/markets-e2e.py
Requires the mock:   python qa/fake_sportybet.py 8123   (in another terminal)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import os  # noqa: E402

# Point the app at the mock before it loads its settings.
os.environ["SPORTYBET_MARKETS_ENABLED"] = "true"
os.environ["SPORTYBET_BASE_URL"] = "http://127.0.0.1:8123/api"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)

print("=== fixture detail still works ===")
r = client.get("/api/fixtures/726")
print("GET /api/fixtures/726 ->", r.status_code)
fx = r.json()
print("  match:", fx["home_team"]["name"], "v", fx["away_team"]["name"])
print("  home badge:", fx["home_team"]["logo"][:90])

print("\n=== sportybet board ===")
r = client.get("/api/fixtures/726/sportybet-markets")
print("GET .../sportybet-markets ->", r.status_code)
if r.status_code != 200:
    print("BODY:", r.text[:600])
    raise SystemExit(1)

board = r.json()
print("available        :", board["available"])
print("reason           :", board.get("reason"))
print("market_count     :", board.get("market_count"))
print("categories       :", len(board.get("categories", [])))
print("model_priced     :", board.get("model_priced_categories"))
print("groups           :", [g["group"] for g in board.get("groups", [])])

print("\n=== category index ===")
for cat in board.get("categories", []):
    flag = "MODEL" if cat["model_priced"] else "book "
    here = "on-match" if cat["present"] else "        "
    print(f"  [{flag}] [{here}] {cat['label']}")

print("\n=== assertions ===")
fails = []


def expect(label, cond):
    if not cond:
        fails.append(label)
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")


labels = [c["label"] for c in board["categories"]]
expect("board is available", board["available"])
expect("33 categories listed", len(labels) == 33)
expect("only 1X2/OU/BTTS are model-priced",
       board["model_priced_categories"] == ["1x2", "ou", "btts"])
for wanted in [
    "1X2 - 1UP", "1X2 - 2UP", "1X2 - Never Down", "Asian Over/Under",
    "Over/Under - Early Goals", "Double Chance - 1UP", "1st Goal",
    "Asian Handicap", "GG/NG 2+",
    "Any Team To Score 2 or More Goals in a Row",
    "Any Team To Score 3 or More Goals in a Row",
    "Home Team To Score 2 or More Goals in a Row",
]:
    expect(f"index lists {wanted!r}", wanted in labels)

# Group ordering must follow the canonical taxonomy, not the feed.
order = [g["group"] for g in board["groups"]]
if "1X2" in order and "GG/NG" in order:
    expect("1X2 group sorts before GG/NG", order.index("1X2") < order.index("GG/NG"))
expect("caveat restricts the claim",
       "1X2, Over/Under and GG/NG" in board.get("caveat", ""))

print("\nRESULT:", "ALL PASSED" if not fails else f"{len(fails)} FAILED -> {fails}")
raise SystemExit(1 if fails else 0)
