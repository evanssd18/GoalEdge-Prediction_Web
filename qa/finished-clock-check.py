"""Unit check for the finished-vs-stoppage rule.

A football match is over well before the clock model's stale bound. Real
stoppage tops out around 10 minutes, so 90 minutes of play + a 15-minute
interval + 10 minutes of stoppage = 115 real minutes. A match inside that
window is genuinely in stoppage; past it the feed has simply not closed the
row, and a clock still reading "90+15'" there is the bug this pins.

Run:  python qa/finished-clock-check.py
"""
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "backend")
from app import flashscore  # noqa: E402

failed = 0


def check(label, got, want):
    global failed
    if got != want:
        print(f"FAIL  {label}: got {got!r}, want {want!r}")
        failed += 1
    else:
        print(f"ok    {label}: {got!r}")


now = datetime(2026, 1, 1, 18, 0, tzinfo=timezone.utc)

# A match in genuine second-half stoppage is still live and shows 90+N'.
live_kick = now - timedelta(minutes=110)
c = flashscore.live_clock(live_kick, now)
check("110 real minutes -> live", c["period"], "2")
check("110 real minutes -> not stale", c["stale"], False)
check("110 real minutes minute", c["minute"], 90)
label = flashscore.live_minute_label(live_kick, now)
check("110 real minutes label", label, f"90+{c['added']}'")

# Past the realistic end of a match (105 + 10 stoppage), the feed has not closed
# the row and the match must read finished, not a stoppage minute.
over_kick = now - timedelta(minutes=118)
c = flashscore.live_clock(over_kick, now)
check("118 real minutes -> stale", c["stale"], True)
check("118 real minutes -> ft", c["period"], "ft")
check("118 real minutes label is None", flashscore.live_minute_label(over_kick, now), None)

# The stale bound must be far enough past 105 to never clip genuine stoppage.
check("bound keeps 10 min stoppage", flashscore.STALE_AFTER_MINUTES >= 115, True)
# And close enough to declare a finished match finished within a few minutes.
check("bound declares finished by 118", flashscore.STALE_AFTER_MINUTES <= 117, True)

print()
if failed:
    print(f"{failed} case(s) failed")
    sys.exit(1)
print("OK  finished-vs-stoppage rule holds")
