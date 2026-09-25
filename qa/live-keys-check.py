"""Hit Odds API and API-Football with the configured keys. Network required."""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.apifootball import injuries_by_team, probe  # noqa: E402
from app.config import settings  # noqa: E402
from app.oddsapi import fetch_sport_odds  # noqa: E402


def mask(value: str | None) -> str:
    if not value:
        return "(missing)"
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}…{value[-4:]} ({len(value)} chars)"


print("ODDS_API_KEY", mask(settings.odds_api_key))
print("API_FOOTBALL_KEY", mask(settings.api_football_key))

odds_ok = False
football_ok = False

print("\n=== Odds API soccer_epl ===")
try:
    events, remaining = fetch_sport_odds("soccer_epl", force=True)
    odds_ok = True
    print(f"ok events={len(events)} remaining={remaining}")
    if events:
        sample = events[0]
        print(
            "sample",
            sample.get("home_team"),
            "vs",
            sample.get("away_team"),
            sample.get("commence_time"),
        )
except Exception as exc:  # noqa: BLE001
    print("FAIL", exc)

print("\n=== API-Football /status ===")
status = probe()
football_ok = bool(status.get("ok"))
safe = {k: v for k, v in status.items() if k != "account"}
if isinstance(status.get("account"), dict):
    safe["subscription"] = (status["account"].get("subscription") or {})
    safe["requests"] = (status["account"].get("requests") or {})
print(json.dumps(safe, default=str, indent=2))

if football_ok:
    grouped = injuries_by_team(days=1, force=True)
    print(f"injuries teams={len(grouped)} players={sum(len(v) for v in grouped.values())}")

print("\n=== summary ===")
print("odds", "OK" if odds_ok else "FAIL")
print("api-football", "OK" if football_ok else "FAIL")
sys.exit(0 if odds_ok else 1)
