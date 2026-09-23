"""QA: the SportyBet board helpers (name matching + market normalisation)."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from app import sportybet  # noqa: E402

fails = 0


def check(label, got, expected):
    global fails
    ok = got == expected
    if not ok:
        fails += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}\n        got={got!r}\n   expected={expected!r}")


print("=== normalise_team_name ===")
cases = [
    ("Como 1907", "como"),
    ("Como", "como"),
    ("Parma", "parma"),
    # "Munchen" vs "Munich" is a real transliteration difference, not an
    # affix — these correctly do NOT collapse. If a feed uses "Bayern
    # Munich" against SportyBet's "Bayern Munchen" the board reports
    # no match rather than silently showing the wrong match's prices.
    ("Bayern Munchen", "bayern munchen"),
    ("Bayern Munich", "bayern munich"),
    ("Borussia M'gladbach", "borussia m gladbach"),
    ("Paris SG", "paris sg"),
    ("Inter Milan", "inter milan"),
    ("St Pauli", "st pauli"),
    ("FC Koln", "koln"),
    ("Arouca", "arouca"),          # must NOT lose a leading 'a'
    ("Arsenal", "arsenal"),        # must NOT lose a leading 'a'
    ("SC Freiburg", "freiburg"),
    ("AC Milan", "milan"),
]
for raw, expected in cases:
    check(f"normalise({raw!r})", sportybet.normalise_team_name(raw), expected)

print("\n=== _market_group delegates to the shared taxonomy ===")
# The group label is NOT chosen here any more: _market_group delegates to
# app.markets so the heading a reader sees and the `model_priced` flag come from
# one classifier and can never disagree. These cases therefore pin the delegation
# itself, which is the part a refactor could silently break -- if sportybet ever
# grows a private bucket table again, the label and the badge can drift apart.
from app import markets  # noqa: E402
for name in [
    "1X2",
    "Match Result",
    "Both Teams to Score",
    "Total Goals Over/Under",
    "Double Chance",
    "Asian Handicap",
    "Correct Score",
    "Half Time / Full Time",
]:
    key = markets.categorise(name)
    check(
        f"_market_group({name!r}) is the taxonomy label for {key!r}",
        sportybet._market_group(name),
        markets.BY_KEY[key].label,
    )

# The three the engine can actually price must land on the model-priced labels,
# because the panel badges those groups "GoalEdge prices this".
for name, key in [("1X2", "1x2"), ("Total Goals Over/Under", "ou"), ("Both Teams to Score", "btts")]:
    check(f"{key!r} is flagged model-priced", markets.BY_KEY[key].model_priced, True)
    check(
        f"{name!r} maps onto the model-priced {key!r}",
        markets.categorise(name),
        key,
    )

# Traps a naive substring matcher gets wrong: the more specific name must win.
for name, expected in [
    ("1X2 - 1UP", "1x2_1up"),
    ("Double Chance - 1UP", "dc_1up"),
    ("Asian Handicap", "asian_handicap"),
    ("Half Time / Full Time", "ht_ft"),
]:
    check(f"categorise({name!r})", markets.categorise(name), expected)

print("\n=== normalise_event_markets ===")
payload = {
    "bCode": 10000,
    "data": {
        "event": {
            "eventId": "SB123",
            "markets": [
                {
                    "id": "18",
                    "desc": "1X2",
                    "outcomes": [
                        {"desc": "Home", "odds": "2.10"},
                        {"desc": "Draw", "odds": "3.40"},
                        {"desc": "Away", "odds": "3.30"},
                        {"desc": "Bad", "odds": "n/a"},  # dropped
                    ],
                },
                {
                    "id": "10",
                    "desc": "Both Teams to Score",
                    "outcomes": [{"desc": "Yes", "odds": 1.72}, {"desc": "No", "odds": 2.05}],
                },
                {"id": "99", "desc": "", "outcomes": [{"desc": "X", "odds": 2.0}]},  # no name
            ],
        }
    },
}
# Named `board` rather than `markets`: the taxonomy module is imported as
# `markets` above, and shadowing it here is how the previous revision of this
# test silently compared against the wrong object.
board = sportybet.normalise_event_markets(payload)
check("market count", len(board), 2)
check("first market name", board[0]["name"], "1X2")
# The raw book name is preserved; the *group* is the canonical taxonomy label.
check("first market group", board[0]["group"], markets.BY_KEY["1x2"].label)
check("group_key is the taxonomy key", board[0]["group_key"], "1x2")
check("unpriceable outcome dropped", len(board[0]["selections"]), 3)
check("string odds parsed", board[0]["selections"][0]["odds"], 2.10)
check("float odds preserved", board[1]["selections"][0]["odds"], 1.72)
check("btts group", board[1]["group"], markets.BY_KEY["btts"].label)

# Every group a market lands in must be a real taxonomy label, so the UI can
# always resolve a heading -- an unknown group would render as an unlabelled box.
known_labels = {c.label for c in markets.CATEGORIES}
for m in board:
    check(
        f"group {m['group']!r} is a known taxonomy label",
        m["group"] in known_labels,
        True,
    )

print("\n=== group_markets buckets by canonical label ===")
bucketed = sportybet.group_markets(board)
check("one bucket per distinct group", len(bucketed), 2)
check("first bucket is the model-priced 1X2 group", bucketed[0]["group"], markets.BY_KEY["1x2"].label)
check("first bucket carries the model-priced flag", bucketed[0]["model_priced"], True)
check("second bucket is GG/NG", bucketed[1]["group"], markets.BY_KEY["btts"].label)
check("group_markets can filter by canonical label",
      [g["group"] for g in sportybet.group_markets(board, [markets.BY_KEY["btts"].label])],
      [markets.BY_KEY["btts"].label])

print("\n=== _as_float rejects non-prices ===")
for raw, expected in [(None, None), (True, None), ("abc", None), ("1.0", None), ("2.50", 2.5), (0.5, None)]:
    check(f"_as_float({raw!r})", sportybet._as_float(raw), expected)

print(f"\n{'ALL PASSED' if fails == 0 else str(fails) + ' FAILED'}")
sys.exit(1 if fails else 0)
