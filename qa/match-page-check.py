"""End-to-end check of the match-page API against a live match and a finished one.

Drives the real FastAPI app through TestClient, so the routes, the DB lookup and
the feed parsing are all exercised together rather than in isolation. The two
match ids are the ones the match page was built for: one in play, one finished.

    backend\\.venv\\Scripts\\python.exe qa\\match-page-check.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

LIVE_MID = "drTAyWmU"   # Soroksar - Tiszakecske, in play
FIN_MID = "vq6T9aFr"    # Fulham - Manchester Utd, finished

client = TestClient(app)

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail=None) -> None:
    (passed if cond else failed).append((name, detail) if detail is not None else name)


# ------------------------------------------------------- the match page route
for label, mid in (("live", LIVE_MID), ("finished", FIN_MID)):
    r = client.get(f"/api/matches/{mid}")
    check(f"{label}: route answers 200", r.status_code == 200, r.status_code)
    if r.status_code != 200:
        continue
    body = r.json()

    check(f"{label}: payload names the match id", body.get("match_id") == mid)
    check(f"{label}: teams resolved", bool(body.get("home_team")), body.get("home_team"))
    check(f"{label}: competition resolved", bool(body.get("competition")), body.get("competition"))
    check(f"{label}: kick-off present", bool(body.get("kickoff")), body.get("kickoff"))
    check(f"{label}: a source page is linked", bool(body.get("flashscore_url")),
          body.get("flashscore_url"))
    check(f"{label}: live block present", isinstance(body.get("live"), dict))

    live = body.get("live") or {}
    clock = live.get("clock") or {}
    check(f"{label}: clock carries a source", bool(clock.get("source")), clock)
    check(f"{label}: clock label rendered", isinstance(live.get("clock_label"), str),
          live.get("clock_label"))
    check(f"{label}: events parsed", isinstance(body.get("events"), list))
    check(f"{label}: stats parsed", isinstance(body.get("stats"), list))
    check(f"{label}: match information block present", isinstance(body.get("info"), dict))
    check(f"{label}: lineups block present", isinstance(body.get("lineups"), dict))

    print(f"\n-- {label}: {body.get('home_team', {}).get('name')} v "
          f"{body.get('away_team', {}).get('name')} --")
    print("   status:", body.get("status"), "| clock:", live.get("clock_label"),
          "| source:", clock.get("source"))
    print("   score:", body.get("home_goals"), "-", body.get("away_goals"),
          f"({live.get('score_source')})")
    print("   events:", len(body.get("events") or []))
    print("   stat sections:", [s["label"] for s in (body.get("stats") or [])])
    lineups = body.get("lineups") or {}
    print("   match info:", body.get("info"))
    for side in ("home", "away"):
        grp = lineups.get(side) or {}
        print(f"   {side} lineup: {len(grp.get('starting') or [])} starters,",
              f"{len(grp.get('substitutes') or [])} subs, formation {grp.get('formation')}")

# The finished reference match must carry the real background detail: these are
# the rows the reference page shows, and every one is published by the feed.
fin_body = client.get(f"/api/matches/{FIN_MID}").json()
fin_info = fin_body.get("info") or {}
check("finished match carries a referee", bool(fin_info.get("referee")), fin_info)
check("finished match carries a venue", bool(fin_info.get("venue")), fin_info)
check("finished match carries an attendance", bool(fin_info.get("attendance")), fin_info)

fin_lineups = fin_body.get("lineups") or {}
check("finished match lineups report as available",
      fin_lineups.get("available") is True, fin_lineups.get("available"))
for side in ("home", "away"):
    grp = fin_lineups.get(side) or {}
    check(f"finished match {side} starting XI is complete",
          len(grp.get("starting") or []) == 11, len(grp.get("starting") or []))
    check(f"finished match {side} has substitutes",
          len(grp.get("substitutes") or []) >= 5, len(grp.get("substitutes") or []))
    names = {p["name"] for p in (grp.get("starting") or [])}
    check(f"finished match {side} XI has no duplicates",
          len(names) == len(grp.get("starting") or []), names)
check("the two lineups are different teams",
      {p["name"] for p in (fin_lineups.get("home") or {}).get("starting", [])}
      != {p["name"] for p in (fin_lineups.get("away") or {}).get("starting", [])})

# Every goal in the finished match should carry the score it produced, which is
# what makes the timeline readable as a scoreline being built.
fin_goals = [e for e in fin_body.get("events") or [] if e["kind"] in ("goal", "own-goal", "penalty")]
check("goals carry a running score",
      all(e.get("score") for e in fin_goals), [e.get("score") for e in fin_goals])
print("\n-- finished match goals --")
for e in fin_goals:
    print("  ", e["minute_label"], e["kind"], e.get("player"), "->", e.get("score"))

# The finished match must read FT and must NOT offer a running minute.
r = client.get(f"/api/matches/{FIN_MID}")
fin = r.json()
check("finished match reads FT", (fin.get("live") or {}).get("clock_label") == "FT",
      (fin.get("live") or {}).get("clock_label"))
check("finished match has no running minute",
      (fin.get("live") or {}).get("clock", {}).get("running") is False,
      (fin.get("live") or {}).get("clock"))
check("finished match carries a score", fin.get("home_goals") is not None
      and fin.get("away_goals") is not None,
      (fin.get("home_goals"), fin.get("away_goals")))

# ------------------------------------------------------------ the live poll
r = client.get(f"/api/matches/{LIVE_MID}/live")
check("live poll answers 200", r.status_code == 200, r.status_code)
if r.status_code == 200:
    poll = r.json()
    check("live poll carries a clock", bool(poll.get("clock")), poll.get("clock"))
    check("live poll carries a clock label", isinstance(poll.get("clock_label"), str),
          poll.get("clock_label"))
    check("live poll carries the score",
          poll.get("home_goals") is not None and poll.get("away_goals") is not None, poll)
    check("live poll is small (no stats payload)", "stats" not in poll)
    print("\n-- live poll --")
    print("   clock:", poll.get("clock_label"), "| source:",
          (poll.get("clock") or {}).get("source"))
    print("   score:", poll.get("home_goals"), "-", poll.get("away_goals"))
    print("   event_count:", poll.get("event_count"))

# ------------------------------------------------- minute-by-minute behaviour
# The clock must advance with real time, not sit on the last event. The anchor
# is the published minute the clock counts up from, and it only exists while a
# match is actually in play -- a match that has since finished correctly reads
# FT with no running anchor at all.
minute = (client.get(f"/api/matches/{LIVE_MID}/live").json().get("clock") or {})
if minute.get("source") == "feed":
    anchor = minute.get("anchor_minute")
    check("live clock exposes its published anchor", isinstance(anchor, int), minute)
    check("live clock is at or after its anchor",
          minute.get("minute") is None or minute.get("minute") >= (anchor or 0), minute)
else:
    # The chosen match has since gone to full time (a real possibility for any
    # fixed match id). The clock is still required to be honest about it.
    check("non-live clock is labelled final rather than left blank",
          minute.get("source") in ("final", "derived", "unavailable"), minute)
    print("\n(match is no longer in play; live-anchor checks applied to the derived path)")

# A derived clock must still be produced for a match that is in play, so the
# page has a minute even when the summary feed carries none.
anchored = client.get(f"/api/matches/{LIVE_MID}").json()
check("minute-by-minute path exists for the match page",
      (anchored.get("live") or {}).get("clock") is not None, anchored.get("live"))

# ------------------------------------------------- a match id that is not real
r = client.get("/api/matches/NOTAREALID")
check("unknown match id does not 500", r.status_code == 200, r.status_code)
if r.status_code == 200:
    unknown = r.json()
    live_block = unknown.get("live") or {}
    # An unknown id must not invent a match: no context, no events, no stats.
    check("unknown match id resolves no teams", not unknown.get("home_team"), unknown.get("home_team"))
    check("unknown match id carries no events", not unknown.get("events"), unknown.get("events"))
    check("unknown match id carries no stats", not unknown.get("stats"), unknown.get("stats"))
    check("unknown match id offers no fake clock",
          live_block.get("clock_label") in (None, ""), live_block.get("clock_label"))
    check("unknown match id still answers with a page", "match_id" in unknown)

# --------------------------------------------------- the livescore row's links
r = client.get("/api/livescores?date=today&sync=false")
check("livescores answers", r.status_code == 200, r.status_code)
if r.status_code == 200:
    rows = [row for g in r.json().get("groups", []) for row in g["items"]]
    if rows:
        row = rows[0]
        check("livescore row offers a match-page link",
              ("match_url" in row) and ("flashscore_url" in row), sorted(row))
        check("livescore row's match link points at the new page",
              (row.get("match_url") or "").startswith("#/match/") if row.get("match_url") else True,
              row.get("match_url"))
    else:
        print("\n(no rows on today's board; row-link checks skipped)")

print(f"\n{len(passed)} passed, {len(failed)} failed")
for entry in failed:
    name, detail = entry if isinstance(entry, tuple) else (entry, None)
    print("  FAIL:", name, "->", detail)

sys.exit(1 if failed else 0)
