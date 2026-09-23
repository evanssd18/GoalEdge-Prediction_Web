"""Does the lineups feed publish a real pitch position, or only a shirt/order?

The existing code refuses to draw a pitch diagram on the grounds that a layout
built from a guess would claim more than the data does. That objection only
holds if the feed gives no coordinates. This checks what it actually sends.

The keys worth understanding:
  LH  -- already read as "position"; on a real payload it is 0..10, which is the
         INDEX in the formation row, not a grid coordinate.
  LG  -- 1 on outfield players, 2 on the keeper (seen in the raw dump).
  LO  -- an integer that was not previously read at all.
  LLLI/LLP -- unknown; probed here.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import flashscore as fs  # noqa: E402

MID = sys.argv[1] if len(sys.argv) > 1 else "tvFgYRrf"
raw = fs._fetch(f"/x/feed/df_li_1_{MID}")
records = [r for r in raw.split(chr(0x7E)) if r.strip()]

# Every key present on a player record, so nothing is being missed.
allkeys: dict[str, int] = {}
for record in records:
    for pair in record.split(chr(0xAC)):
        key, _, value = pair.partition(chr(0xF7))
        if key:
            allkeys[key.lstrip("~")] = allkeys.get(key.lstrip("~"), 0) + 1

print("every key on the feed, with a count:")
for key, count in sorted(allkeys.items()):
    print(f"  {key:<6} {count:>3}")

print()
print("starting-XI records, key by key (home side):")
seen_home = False
for record in records:
    fields: dict[str, str] = {}
    for pair in record.split(chr(0xAC)):
        key, _, value = pair.partition(chr(0xF7))
        if key:
            fields.setdefault(key.lstrip("~"), value)
    if (fields.get("LB") or "").strip() == "Starting Lineups":
        seen_home = True
        continue
    if not seen_home:
        continue
    name = (fields.get("LI") or "").strip()
    if not name:
        # The bare `LC=2` marker opens the away list.
        if (fields.get("LC") or "").strip() == "2":
            break
        continue
    interesting = {k: fields.get(k) for k in ("LH", "LG", "LO", "LJ", "LS", "LR", "LK", "LL") if fields.get(k)}
    print(f"  {name:<20} {interesting}")
