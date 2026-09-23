"""Decode LO against the known formation, to test whether it is a pitch axis.

The formation is published (1-4-2-3-1), so the number of players per line is
known. If LO is a real coordinate it must group the XI into exactly those
lines -- and LG=3 must land on the keeper. Anything else means LO is something
else (a distance, a rating, an id) and must NOT be used to place players.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import flashscore as fs  # noqa: E402

MID = sys.argv[1] if len(sys.argv) > 1 else "tvFgYRrf"
raw = fs._fetch(f"/x/feed/df_li_1_{MID}")
records = [r for r in raw.split(chr(0x7E)) if r.strip()]

# Walk the feed, collecting each side's starting XI in record order.
sections: dict[str, list[dict]] = {"home": [], "away": []}
side: str | None = None
section = "starting"
formation: dict[str, str] = {}
for record in records:
    fields: dict[str, str] = {}
    for pair in record.split(chr(0xAC)):
        key, _, value = pair.partition(chr(0xF7))
        if key:
            fields.setdefault(key.lstrip("~"), value)
    block = (fields.get("LB") or fields.get("LA") or "").strip()
    name_only = not fields.get("LP") and not fields.get("LI")
    if name_only and fields.get("LC"):
        side = "home" if (fields.get("LC") or "").strip() == "1" else "away"
    if block and name_only:
        section = "starting" if block == "Starting Lineups" else "other"
        continue
    if name_only or not fields.get("LI") or side is None:
        continue
    if section != "starting":
        continue
    if fields.get("LD", "").strip():
        formation[side] = fields["LD"].strip()
    sections[side].append(
        {
            "name": (fields.get("LI") or "").strip(),
            "LH": fields.get("LH"),
            "LG": fields.get("LG"),
            "LO": fields.get("LO"),
            "LL": fields.get("LL"),
            "shirt": fields.get("LJ"),
        }
    )

for side in ("home", "away"):
    rows = sections[side]
    print(f"\n=== {side} ({len(rows)} players) formation {formation.get(side)} ===")
    print(f"{'name':<20} {'LH':>3} {'LG':>3} {'LO':>5} {'LL':>4}")
    for p in rows:
        print(f"{p['name']:<20} {p['LH']:>3} {p['LG']:>3} {p['LO']:>5} {p['LL']:>4}")

    # Is LL simply 1..11, i.e. an ordering rather than a coordinate?
    try:
        lls = sorted(int(p["LL"]) for p in rows if p["LL"])
        print(f"  LL sorted: {lls}")
    except (TypeError, ValueError):
        pass
    try:
        los = sorted(int(p["LO"]) for p in rows if p["LO"])
        print(f"  LO sorted: {los}")
    except (TypeError, ValueError):
        pass
    # If LO were a fraction of the pitch it would be bounded; 200+ says it is not.
    try:
        vals = [int(p["LO"]) for p in rows if p["LO"]]
        print(f"  LO range: {min(vals)}..{max(vals)}  distinct={len(set(vals))}")
    except (TypeError, ValueError):
        pass
