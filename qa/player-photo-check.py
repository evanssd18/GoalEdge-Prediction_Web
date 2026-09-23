"""Player portraits on the lineups: parsing, URL shape, and the API.

The regression this pins is silent: the photo fields are six independent sizes
(`LPI`/`LPL`/`LPQ`/`LPX`/`LPY`/`LPZ`) and the parser reads none of them by
default, so a rename or a handling change drops every face while names, shirts
and formations still render perfectly -- the page looks fine at a glance.

Offline for the shape assertions (a captured payload), live for the rest.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from app import matchdetail as md  # noqa: E402

PASS = FAIL = 0


def check(label: str, cond: bool) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {label}")


# --- the six sizes, and the URL the parser builds from each ------------------
print("-- photo fields --")

# A record shaped like the real feed: alternating KEY<0xF7>VALUE pairs joined
# by the 0xAC separator. Keys are passed in order rather than as kwargs, because
# the feed's meaning depends on which keys are present and a dict would also
# sort them, which is not how a real record is laid out.
def record(**fields: str) -> str:
    return chr(0xAC).join(f"{k}{chr(0xF7)}{v}" for k, v in fields.items())


base = md._PLAYER_PHOTO_BASE
check("base is the flashscore image host", base == "https://static.flashscore.com/res/image/data/")

# Each size key, on its own, must produce a URL -- none may be silently ignored.
for key in ("LPI", "LPL", "LPQ", "LPX", "LPY", "LPZ"):
    _row, _hi = md._player_photo({key: f"abc123-{key}.png"})
    check(f"{key} yields a URL", _row == f"{base}abc123-{key}.png")

# The row prefers LPL (72px) and the 2x source is LPQ (126px), as measured.
f = {"LPL": "row.png", "LPQ": "hi.png"}
check("row uses LPL", md._player_photo(f)[0] == f"{base}row.png")
check("hires uses LPQ", md._player_photo(f)[1] == f"{base}hi.png")

# Only a tiny size present: the row must still get a picture rather than a gap.
check("falls back to a smaller size", md._player_photo({"LPI": "small.png"})[0] == f"{base}small.png")

# No size at all -> (None, None), which is what triggers the initials fallback.
check("no fields -> None", md._player_photo({}) == (None, None))
check("empty strings -> None", md._player_photo({"LPL": "  ", "LPQ": ""}) == (None, None))

# A value that already carries a scheme must not be prefixed a second time.
full = "https://example.test/x.png"
check("absolute URL is left alone", md._player_photo({"LPL": full})[0] == full)
check("protocol-relative gets a scheme", md._player_photo({"LPL": "//cdn.test/x.png"})[0] == "https://cdn.test/x.png")
check("leading slash does not double up", md._player_photo({"LPL": "/x.png"})[0] == f"{base}x.png")

# --- a real record through the full parser ----------------------------------
print("-- parser end to end --")
name = "Test Player"
raw = chr(0x7E).join([
    record(LA="Formation", LB="Starting Lineups", LC="1"),
    record(LI=name, LD="1-4-2-3-1", LH="1", LJ="7", LPL="aa-bb.png", LPQ="cc-dd.png"),
])
parsed = md.parse_lineups(raw)
players = parsed["home"]["starting"]
check("one starter parsed", len(players) == 1)
if players:
    p = players[0]
    check("name survives", p["name"] == name)
    check("shirt survives", p["shirt"] == 7)
    check("photo key is always present", "photo" in p)
    check("photo_hires key is always present", "photo_hires" in p)
    check("photo url built", p["photo"] == f"{base}aa-bb.png")
    check("hires url built", p["photo_hires"] == f"{base}cc-dd.png")

# The keys must exist even when there is no picture, so the renderer can read
# them without a guard.
nophoto = md.parse_lineups(chr(0x7E).join([
    record(LA="Formation", LB="Starting Lineups", LC="1"),
    record(LI="No Face", LD="1-4-4-2", LH="1", LJ="9"),
]))
row = nophoto["home"]["starting"][0]
check("missing photo is None, not absent", row["photo"] is None and "photo" in row)

# --- live: the portraits must actually resolve ------------------------------
print("-- live feed --")
MID = sys.argv[1] if len(sys.argv) > 1 else "tvFgYRrf"
try:
    live = md.fetch_lineups(MID)
    total = withp = 0
    for side in ("home", "away"):
        for p in live[side]["starting"] + live[side]["substitutes"]:
            total += 1
            if p["photo"]:
                withp += 1
    print(f"  match {MID}: {withp}/{total} players carry a portrait")
    check("the feed returned players", total > 0)
    check("most players have a portrait", withp >= total * 0.8)
    # A portrait URL that 404s would leave a broken image the UI cannot detect,
    # so one is fetched for real.
    import urllib.request

    sample = next(
        p["photo"]
        for side in ("home", "away")
        for p in live[side]["starting"]
        if p["photo"]
    )
    req = urllib.request.Request(sample, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        body = resp.read()
    check("a portrait URL returns a PNG", body[:8].startswith(b"\x89PNG"))
    print(f"  fetched {len(body)}B from {sample.rsplit('/', 1)[-1]}")
except Exception as exc:  # noqa: BLE001
    check(f"live lineup feed reachable ({type(exc).__name__}: {exc})", False)

print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
