"""Unit checks for the Flashscore history parser (app/history.py).

The parser turns a raw ``df_hh`` payload into finished matches, and those
matches become the model's rating history -- so a parsing error here silently
produces wrong team ratings and wrong predictions. Each case below is a small
hand-built payload with a known correct answer, including the traps:

* a row with no scoreline        -> skipped, never recorded as 0-0
* a row with no teams            -> skipped
* the same match listed twice    -> de-duplicated, not double-counted
* head-to-head vs team form      -> routed to the right list

Run from the repo root:  python qa/history-check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.history import parse_history  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FIELD = "\xac"
KV = "\xf7"
RECORD = "\x7e"


def row(**kv) -> str:
    return FIELD.join(f"{k}{KV}{v}" for k, v in kv.items())


def payload(*records: str) -> str:
    return RECORD.join(records)


CASES = []

# --- 1. a normal team form block -------------------------------------------
CASES.append(
    (
        "team form rows are parsed",
        payload(
            row(KB="Last matches: Ajax"),
            row(KC="1700000", FH="Ajax", FK="PSV", KU="3", KT="1",
                KF="Eredivisie", KS="home", WIS="w"),
            row(KC="1699000000", FH="Feyenoord", FK="Ajax", KU="2", KT="2",
                KF="Eredivisie", KS="away", WIS="d"),
        ),
        lambda form, h2h: (
            len(form) == 2
            and len(h2h) == 0
            and form[0].home_goals == 3
            and form[0].away_goals == 1
            and form[0].venue == "home"
            and form[1].result == "d"
        ),
    )
)

# --- 2. a match with no scoreline is skipped, not recorded as 0-0 ----------
CASES.append(
    (
        "scoreless rows are skipped, never 0-0",
        payload(
            row(KB="Last matches: Ajax"),
            row(KC="1700000", FH="Ajax", FK="PSV", KF="Eredivisie"),
        ),
        lambda form, h2h: len(form) == 0,
    )
)

# --- 3. a row missing team names is skipped --------------------------------
CASES.append(
    (
        "rows without teams are skipped",
        payload(
            row(KB="Last matches: Ajax"),
            row(KC="1700000", FH="", FK="", KU="1", KT="0"),
        ),
        lambda form, h2h: len(form) == 0,
    )
)

# --- 4. duplicates collapse to one -----------------------------------------
CASES.append(
    (
        "the same match listed twice counts once",
        payload(
            row(KB="Last matches: Ajax"),
            row(KC="1700000", FH="Ajax", FK="PSV", KU="3", KT="1", KF="Eredivisie"),
            row(KC="1700000", FH="Ajax", FK="PSV", KU="3", KT="1", KF="Eredivisie"),
        ),
        lambda form, h2h: len(form) == 1,
    )
)

# --- 5. head-to-head is separated from team form ---------------------------
CASES.append(
    (
        "head-to-head rows land in the h2h list",
        payload(
            row(KB="Last matches: Ajax"),
            row(KC="1700000", FH="Ajax", FK="PSV", KU="1", KT="0", KF="Eredivisie"),
            row(KB="Head-to-head matches"),
            row(KC="1690000", FH="PSV", FK="Ajax", KU="0", KT="2", KF="Eredivisie"),
        ),
        lambda form, h2h: len(form) == 1 and len(h2h) == 1 and h2h[0].home_team == "PSV",
    )
)

# --- 6. an empty payload yields nothing, and does not raise -----------------
CASES.append(
    (
        "empty payload yields empty lists",
        "",
        lambda form, h2h: form == [] and h2h == [],
    )
)

# --- 7. a draw is parsed as a draw -----------------------------------------
CASES.append(
    (
        "draws keep equal goals",
        payload(
            row(KB="Last matches: X"),
            row(KC="1700000", FH="A", FK="B", KU="0", KT="0", KF="L"),
        ),
        lambda form, h2h: len(form) == 1 and form[0].home_goals == 0 and form[0].away_goals == 0,
    )
)


def main() -> int:
    failures = []
    for name, raw, check in CASES:
        form, h2h = parse_history(raw)
        try:
            ok = bool(check(form, h2h))
        except Exception as exc:  # noqa: BLE001
            ok = False
            name = f"{name} (raised {exc!r})"
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            failures.append(name)

    print()
    if failures:
        print(f"{len(failures)} FAILED:", failures)
        return 1
    print(f"ALL PASS ({len(CASES)} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
