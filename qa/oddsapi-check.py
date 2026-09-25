"""QA: Odds API matching and consensus prices. No network."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.oddsapi import (  # noqa: E402
    canonical_team,
    extract_consensus,
    kickoff_close,
    names_match,
    sport_key_for,
)

fails = 0


def check(label, got, expected):
    global fails
    ok = got == expected
    if not ok:
        fails += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}\n        got={got!r}\n   expected={expected!r}")


print("=== canonical_team ===")
for raw, expected in [
    ("Manchester United", "manchester united"),
    ("Man Utd", "manchester united"),
    ("Man City", "manchester city"),
    ("Tottenham Hotspur", "tottenham"),
    ("Spurs", "tottenham"),
    ("Paris SG", "paris saint germain"),
    ("PSG", "paris saint germain"),
    ("Inter Milan", "inter"),
    ("Internazionale", "inter"),
    ("Bayern München", "bayern munich"),
    ("FC Köln", "cologne"),
    ("Wolves", "wolverhampton"),
    ("Nottingham Forest", "nottingham forest"),
    ("Brighton and Hove Albion", "brighton"),
    ("AC Milan", "milan"),
]:
    check(f"canonical({raw!r})", canonical_team(raw), expected)

print("\n=== names_match ===")
check(
    "Man Utd vs Manchester United",
    names_match("Manchester United", "Chelsea", "Man Utd", "Chelsea"),
    True,
)
check(
    "different clubs do not match",
    names_match("Arsenal", "Chelsea", "Arsenal", "Tottenham"),
    False,
)

print("\n=== sport_key_for ===")
def comp(name, country="England"):
    return SimpleNamespace(
        name=name,
        country=SimpleNamespace(name=country) if country else None,
    )

check("EPL", sport_key_for(comp("Premier League")), "soccer_epl")
check("Jamaica PL is not EPL", sport_key_for(comp("Premier League", "Jamaica")), None)
check("Premier League 2 is not EPL", sport_key_for(comp("Premier League 2")), None)
check("Championship", sport_key_for(comp("Championship")), "soccer_efl_champ")
check("LaLiga", sport_key_for(comp("LaLiga", "Spain")), "soccer_spain_la_liga")
check("Serie B before A", sport_key_for(comp("Serie B", "Italy")), "soccer_italy_serie_b")
check("Bundesliga 2 before 1", sport_key_for(comp("Bundesliga 2", "Germany")), "soccer_germany_bundesliga2")
check("UCL", sport_key_for(comp("Champions League", "Europe")), "soccer_uefa_champs_league")
check("UCL women excluded", sport_key_for(comp("Champions League Women", "Europe")), None)
check("unknown cup", sport_key_for(comp("County Cup", "England")), None)

print("\n=== kickoff_close ===")
t0 = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)
check("same instant", kickoff_close(t0, t0, 8), True)
check("6h ok", kickoff_close(t0, t0 + timedelta(hours=6), 8), True)
check("10h no", kickoff_close(t0, t0 + timedelta(hours=10), 8), False)
check("naive vs aware", kickoff_close(t0, datetime(2026, 10, 11, 15, 0), 8), True)

print("\n=== extract_consensus ===")
event = {
    "home_team": "Arsenal",
    "away_team": "Chelsea",
    "commence_time": "2026-10-11T15:00:00Z",
    "bookmakers": [
        {
            "key": "williamhill",
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": "Arsenal", "price": 1.9},
                        {"name": "Draw", "price": 3.6},
                        {"name": "Chelsea", "price": 4.0},
                    ],
                },
                {
                    "key": "totals",
                    "outcomes": [
                        {"name": "Over", "point": 2.5, "price": 1.8},
                        {"name": "Under", "point": 2.5, "price": 2.0},
                    ],
                },
            ],
        },
        {
            "key": "pinnacle",
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": "Arsenal", "price": 1.95},
                        {"name": "Draw", "price": 3.7},
                        {"name": "Chelsea", "price": 4.2},
                    ],
                }
            ],
        },
    ],
}
c = extract_consensus(event)
check("has_1x2", c["has_1x2"], True)
check("median home", c["odds_home"], 1.92)
check("median draw", c["odds_draw"], 3.65)
check("median away", c["odds_away"], 4.1)
check("over 2.5 from the one book that listed it", c["odds_over25"], 1.8)
check("bookmaker prefers pinnacle", "pinnacle" in c["bookmaker"], True)

# Totals at 1.5 must not be treated as 2.5.
event_wrong_line = {
    "home_team": "A",
    "away_team": "B",
    "bookmakers": [
        {
            "key": "x",
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": "A", "price": 2.0},
                        {"name": "Draw", "price": 3.0},
                        {"name": "B", "price": 4.0},
                    ],
                },
                {
                    "key": "totals",
                    "outcomes": [
                        {"name": "Over", "point": 1.5, "price": 1.2},
                        {"name": "Under", "point": 1.5, "price": 4.5},
                    ],
                },
            ],
        }
    ],
}
c2 = extract_consensus(event_wrong_line)
check("ignore non-2.5 totals", c2["odds_over25"], None)

print("\n=== done ===")
sys.exit(1 if fails else 0)
