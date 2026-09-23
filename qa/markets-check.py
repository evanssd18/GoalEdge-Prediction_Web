"""QA: the market taxonomy classifies bookmaker names correctly.

The names below are the real category labels from the reference market list,
plus the variants feeds actually emit.
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import markets  # noqa: E402

fails = 0


def check(raw, expected_key, expected_model):
    global fails
    key = markets.categorise(raw)
    model = markets.is_model_priced(raw)
    ok = key == expected_key and model == expected_model
    if not ok:
        fails += 1
    label = markets.BY_KEY[key].label
    print(
        f"[{'PASS' if ok else 'FAIL'}] {raw!r:52} -> {key:18} model={model!s:5} "
        f"label={label!r}"
    )


print("=== the reference category list ===")
check("1X2", "1x2", True)
check("1X2 - 1UP", "1x2_1up", False)
check("1X2 - 2UP", "1x2_2up", False)
check("1X2 - Never Down", "1x2_never_down", False)
check("Over/Under", "ou", True)
check("Asian Over/Under", "asian_ou", False)
check("Over/Under - Early Goals", "ou_early", False)
check("Double Chance", "dc", False)
check("Double Chance - 1UP", "dc_1up", False)
check("1st Goal", "first_goal", False)
check("Handicap", "handicap", False)
check("Asian Handicap", "asian_handicap", False)
check("GG/NG", "btts", True)
check("GG/NG 2+", "btts_2plus", False)
check("Any Team To Score 2 or More Goals in a Row", "streak_any_2", False)
check("Any Team To Score 3 or More Goals in a Row", "streak_any_3", False)
check("Home Team To Score 2 or More Goals in a Row", "streak_home_2", False)

print("\n=== feed variants ===")
check("Both Teams to Score", "btts", True)
check("Total Goals Over/Under", "ou", True)
check("Match Result", "1x2", True)
check("Draw No Bet", "other", False)
check("Correct Score", "correct_score", False)
check("Half Time / Full Time", "ht_ft", False)  # its own market, not half_time
check("", "other", False)

print("\n=== new model-only markets ===")
check("1st Half O/U", "ou_1st_half", False)
check("Handicap 1st Half", "handicap_1st_half", False)
check("Double Chance & GG/NG", "dc_btts", False)
check("Double Chance & Over/Under", "dc_ou25", False)
check("1X2 & Over/Under", "1x2_ou25", False)
check("1X2 From 15 to 30 Minutes", "goal_window", False)

print("\n=== the precedence traps ===")
# These are the cases a naive substring classifier gets wrong.
check("1X2 - 1UP", "1x2_1up", False)          # must not fall through to 1x2
check("Double Chance - 1UP", "dc_1up", False)  # must not become 1x2 via "1UP"
check("Asian Handicap", "asian_handicap", False)  # must not become plain handicap
# The combo names embed the names of the markets they combine, so each one used
# to be swallowed by the single-market branch underneath it.
check("Double Chance & GG/NG", "dc_btts", False)      # not btts
check("1X2 & Over/Under", "1x2_ou25", False)         # not dc_ou25
check("1X2 From 1 to 15 Minutes", "goal_window", False)  # not 1x2
check("1st Half O/U", "ou_1st_half", False)          # not ou
check("Handicap 1st Half", "handicap_1st_half", False)  # not handicap
check("Asian Over/Under", "asian_ou", False)  # must not become plain ou
check("GG/NG 2+", "btts_2plus", False)        # must not become plain btts

print("\n=== panel payload shape ===")
payload = markets.category_payload()
model_priced = [c["key"] for c in payload if c["model_priced"]]
print(f"categories      : {len(payload)}")
print(f"model-priced    : {model_priced}")
assert model_priced == ["1x2", "ou", "btts"], f"unexpected: {model_priced}"
# Ordering must be stable and match the canonical list.
labels = [c["label"] for c in payload]
assert labels[0] == "1X2", labels[0]
print(f"first category  : {labels[0]!r}")

print(f"\n{'ALL PASSED' if fails == 0 else str(fails) + ' FAILED'}")
sys.exit(1 if fails else 0)
