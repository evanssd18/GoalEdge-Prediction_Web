"""QA: API-Football injury shading. No network."""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.apifootball import shade_lambdas, team_availability  # noqa: E402

fails = 0


def check(label, got, expected, *, close=False):
    global fails
    if close:
        ok = abs(float(got) - float(expected)) < 1e-6
    else:
        ok = got == expected
    if not ok:
        fails += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}\n        got={got!r}\n   expected={expected!r}")


print("=== team_availability ===")
empty = team_availability("Arsenal", {})
check("no injuries attack", empty["attack_mult"], 1.0)
check("no injuries defence", empty["defence_mult"], 1.0)
check("no injuries notes", empty["notes"], [])

many = {
    "arsenal": [
        {"player": "A", "position": "midfielder", "type": "Missing Fixture", "reason": "Knock"},
        {"player": "B", "position": "midfielder", "type": "Missing Fixture", "reason": "Knock"},
        {"player": "C", "position": "goalkeeper", "type": "Missing Fixture", "reason": "Injury"},
        {"player": "D", "position": "attacker", "type": "Missing Fixture", "reason": "Injury"},
    ]
}
adj = team_availability("Arsenal", many)
check("attack cut when missing attackers", adj["attack_mult"] < 1.0, True)
check("defence raised when GK out", adj["defence_mult"] > 1.0, True)
check("attack floor", adj["attack_mult"] >= 0.90, True)
check("defence ceiling", adj["defence_mult"] <= 1.10, True)
check("notes mention absences", any("absences" in n for n in adj["notes"]), True)
check("notes mention goalkeeper", any("goalkeeper" in n.lower() for n in adj["notes"]), True)

print("\n=== shade_lambdas ===")
lam_h, lam_a, notes = shade_lambdas(1.6, 1.1, "Arsenal", "Chelsea", many)
check("home lambda drops when arsenal depleted", lam_h < 1.6, True)
check("away lambda rises when arsenal GK out", lam_a > 1.1, True)
check("notes attached", bool(notes), True)

print("\n=== done ===")
sys.exit(1 if fails else 0)
