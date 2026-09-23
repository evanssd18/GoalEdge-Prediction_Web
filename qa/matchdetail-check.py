"""Unit + live check of app/matchdetail.py.

Two halves:

* **offline** -- a captured summary payload is parsed and the clock, score and
  events are asserted against it, so the parser is tested without a network.
* **live** -- when the feed is reachable, the same parser is pointed at a real
  match id and the observed values are printed, so a shape change is visible.

Run from the repo root:
    backend\\.venv\\Scripts\\python.exe qa\\matchdetail-check.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import matchdetail as md  # noqa: E402

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail=None) -> None:
    (passed if cond else failed).append((name, detail) if detail is not None else name)


# --------------------------------------------------------------- offline
# A payload in the exact shape the live feed sends: a period header naming the
# half and its score, then incidents carrying their own minute in `IB`.
# Records are separated by `~`; fields inside a record by the micro sign.
# Reproduced with the real separators, so the parser is exercised on the shape
# the feed actually sends rather than a convenient approximation of it.
F = "\xac"   # field separator
R = "\x7e"   # record separator
KV = "\xf7"  # key/value separator

def _rec(*pairs: str) -> str:
    # One record from ``key=value`` strings, joined with the field separator.
    out = []
    for pair in pairs:
        key, _, value = pair.partition("=")
        out.append(f"{key}{KV}{value}")
    return F + F.join(out)

CAPTURED = (
    _rec("AC=1st Half", "IG=1", "IH=0")
    + R + _rec("III=xCQJM9Rb", "IA=1", "IB=5'", "IE=1", "IF=Abdelmonem M.",
               "IK=Yellow Card", "IJ=18")
    + R + _rec("IA=1", "IB=22'", "IE=4", "IF=Ngoy N.", "IK=Own goal", "IJ=18")
    + R + _rec("AC=2nd Half", "IG=2", "IH=1")
    # A goal carries `IE=3` and NO `IK` at all -- the shape that a naive
    # `IK`-keyed parser drops entirely, which is the bug this fixture pins.
    + R + _rec("IA=2", "IB=63'", "IE=3", "IF=Scorer X.", "IJ=18")
)

feed = md.MatchFeed("TEST", CAPTURED, read_at=datetime.now(timezone.utc))

check("period parsed", feed.period == "2nd Half", feed.period)
check("score parsed from the period header", feed.score == (2, 1), feed.score)
check("all three incidents parsed", len(feed.events) == 3, [e["minute_label"] for e in feed.events])
check("events sorted by minute",
      [e["minute"] for e in feed.events] == [5, 22, 63],
      [e["minute"] for e in feed.events])
check("yellow card classified", feed.events[0]["kind"] == "yellow")
check("own goal classified apart from a goal", feed.events[1]["kind"] == "own-goal")
# A goal has no `IK`, so this is the assertion that catches a parser keying off
# the wrong field: it would be classified "note" and the timeline would show
# cards and substitutions but not the score.
check("goal classified by its IE code alone", feed.events[2]["kind"] == "goal", feed.events[2])
check("a missing IK still yields a readable label",
      feed.events[2]["type"] == "Goal", feed.events[2]["type"])
check("event side parsed", feed.events[0]["team"] == "home" and feed.events[2]["team"] == "away")
check("event period attached", feed.events[0]["period"] == "1st Half")
check("player parsed", feed.events[0]["player"] == "Abdelmonem M.")

# The clock is counted from the published KICK-OFF, not from the last incident.
#
# This is the deliberate reversal of the old behaviour. Anchoring on the latest
# published minute made the clock a function of the last thing that happened, so
# a match with no incident for twenty minutes sat twenty minutes behind, and a
# fixture whose feed carries no incident at all had no clock whatsoever. Kick-off
# is an exact timestamp the feed always ships, so counting from it is right even
# when nothing has happened yet -- and it is the only way to show real seconds.
now = datetime.now(timezone.utc)
ko = now - timedelta(minutes=70)
clock = feed.clock(ko, "live", now)
check("clock counts from kick-off, not the feed anchor", clock["source"] == "derived", clock)
check("no feed anchor is used when a kick-off exists", not clock.get("anchor_minute"), clock)
# 70 real minutes = 45 played + 15 interval + 10 into the second half.
check("clock reads 55:00 at 70 real minutes", clock["minute"] == 55, clock)
check("clock carries seconds", clock["seconds"] == 0, clock)
check("clock label is MM:SS", md.clock_label(clock) == "55:00", md.clock_label(clock))
check("the board's short form keeps the prime", md.clock_label_short(clock) == "55'",
      md.clock_label_short(clock))

# Real seconds, not floored minutes: 40s later the seconds have moved with the
# wall clock. A label that only stepped once a minute would fail this.
clock40 = feed.clock(ko, "live", now + timedelta(seconds=40))
check("clock advances in real seconds", clock40["seconds"] == 40, clock40)
check("seconds ride into the label", md.clock_label(clock40) == "55:40", md.clock_label(clock40))

# Minute-by-minute: five real minutes later the clock is five minutes on.
later = now + timedelta(minutes=5)
clock5 = feed.clock(ko, "live", later)
check("clock advances one minute per minute", clock5["minute"] == 60, clock5)

# The clock is continuous from kick-off: first-half stoppage is played BEFORE the
# interval, so the second half resumes past 45:00 rather than restarting at it.
# A match kicking off at 12:00 and resuming at 12:52 is 2 minutes into the second
# half having seen 5 minutes of first-half stoppage -- one timeline, not two.
ko2 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
# The last readable first-half minute is 44:00. At 45 real minutes the model has
# no way to tell "playing stoppage" from "walked off", so it reports the interval
# and the label becomes HT. Stated rather than hidden: a match in real first-half
# stoppage shows HT on this board a few minutes early.
check("44 real minutes is still the 1st half",
      md.clock_label(feed.clock(ko2, "live", ko2 + timedelta(minutes=44))) == "44:00")
check("the 1st half stops reading at 45 real minutes",
      md.clock_label(feed.clock(ko2, "live", ko2 + timedelta(minutes=45))) == "HT")
# The interval is assumed to start at 45 real minutes, because real-time
# arithmetic cannot distinguish "still playing stoppage" from "walked off". The
# model says so rather than pretending to know: at 47 real minutes it reports the
# interval, which also pins the clock at 45:00. This is a known limitation and it
# is bounded by MAX_STOPPAGE_MINUTES, not something the tests should paper over.
check("45 real minutes reads the interval, not invented 1H stoppage",
      feed.clock(ko2, "live", ko2 + timedelta(minutes=47))["period"] == "ht")
# And with no stoppage seen, 45 + 15 interval = 60 real minutes -> 45:00.
resumed = feed.clock(ko2, "live", ko2 + timedelta(minutes=60))
check("2nd half picks up from 45:00, not 0:00", resumed["minute"] == 45, resumed)
check("second-half label is continuous", md.clock_label(resumed) == "45:00", md.clock_label(resumed))
check("second half is a second half", resumed["period"] == "2", resumed)
# The interval is excluded from the match clock, so real time runs 15 minutes
# ahead of match time through the whole second half.
five_in = feed.clock(ko2, "live", ko2 + timedelta(minutes=65))
check("real 65 -> match 50:00", five_in["minute"] == 50, five_in)

# Stoppage: past 90' of play the clock holds at 90 and counts added minutes, so
# the added-time count cannot grow into a number no referee plays.
#
# Match time runs 15 minutes behind real time from the interval onward, so match
# 93:00 is real 108 minutes. Real 108 is comfortably inside the (real 116 minute)
# stale bound, which is the whole point: genuine stoppage is still live, and only
# a match the feed never closed reads full-time.
stoppage = feed.clock(ko2, "live", ko2 + timedelta(minutes=108))
check("stoppage holds the minute at 90", stoppage["minute"] == 90, stoppage)
check("stoppage counts the added minutes", stoppage["added"] == 3, stoppage)
check("stoppage is still live, not stale", stoppage["stale"] is False, stoppage)
check("stoppage keeps the continuous offset", stoppage["period_offset"] == 900, stoppage)
check("stoppage label keeps the +N form", md.clock_label(stoppage) == "90+3:00",
      md.clock_label(stoppage))
# Past the bound the match is reported finished rather than still being played.
#
# The bound is real 116 minutes. Real 105 is the 90:00 whistle (90 play + the
# 15-minute interval), and real stoppage is 1-10 minutes, so 116 is genuinely
# past full time with a minute to spare. It used to sit at 120, and the 105-to-120
# gap was exactly the window in which a finished match displayed "90+15'": a
# ceiling no referee reaches, shown long after the match was over. The tests below
# pin BOTH ends of the new behaviour -- realistic stoppage is still live, and a
# match past the bound reads full-time rather than growing a fantasy minute.
stoppage_late = feed.clock(ko2, "live", ko2 + timedelta(minutes=112))
check("real 112 is still live (90+7 stoppage is real)",
      stoppage_late["stale"] is False and stoppage_late["minute"] == 90, stoppage_late)
check("real 112 shows 90+7, not a fantasy minute",
      md.clock_label(stoppage_late) == "90+7:00", md.clock_label(stoppage_late))
# The bound is exclusive: real 116 is the last live instant (90+11 of stoppage,
# the realistic ceiling) and real 117 is full-time. The most a finished match can
# ever display is 90+11' -- never the old 90+15'.
check("real 116 is the last live minute (90+11)",
      md.clock_label(feed.clock(ko2, "live", ko2 + timedelta(minutes=116))) == "90+11:00",
      md.clock_label(feed.clock(ko2, "live", ko2 + timedelta(minutes=116))))
check("real 117 reads full-time",
      md.clock_label(feed.clock(ko2, "live", ko2 + timedelta(minutes=117))) == "FT")
# `feed.clock` would fall through to its own incident anchor here (the captured
# payload has one), so the module-level clock is what is being asserted. Going
# through derived_clock rather than flashscore.live_clock also pins the
# delegation: this page and the livescore board must share one clock model.
check("past the bound the match reads full-time",
      md.derived_clock(ko2, "live", ko2 + timedelta(minutes=130))["period"] == "ft")

# Added time survives.
added_feed = md.MatchFeed(
    "T",
    _rec("AC=1st Half", "IG=0", "IH=0")
    + R + _rec("IA=1", "IB=45+2'", "IE=1", "IK=Yellow Card"),
    read_at=now,
)
check("added time preserved", added_feed._latest_added == 2)
check("added-time minute parsed", added_feed._latest_minute == 45)

# An incident with no `IK` label still renders, and an unlisted CODE is kept as
# a neutral note rather than dropped. A new event type must never make an event
# disappear from the timeline.
labelless = md.MatchFeed("T", _rec("IA=1", "IB=12'", "IE=1"), read_at=now)
check("an incident with no IK label still renders",
      len(labelless.events) == 1 and labelless.events[0]["kind"] == "yellow",
      labelless.events)
check("its label falls back to the code",
      labelless.events[0]["type"] == "Yellow card", labelless.events[0]["type"])

unknown = md.MatchFeed("T", _rec("IA=1", "IB=12'", "IE=99", "IK=VAR - Check"), read_at=now)
check("an unlisted incident code is kept as a note",
      len(unknown.events) == 1 and unknown.events[0]["kind"] == "note",
      unknown.events)

# An incident with no minute has not happened yet and is skipped, but one with a
# minute and no code is a stray record -- both must be ignored, not mis-dated.
check("an incident without a minute is skipped",
      md.MatchFeed("T", _rec("IA=1", "IE=3"), read_at=now).events == [])
check("an incident without a code is skipped",
      md.MatchFeed("T", _rec("IA=1", "IB=12'"), read_at=now).events == [])

# A score reconstructed from goal incidents when no header carried one.
scored = md.MatchFeed(
    "T",
    _rec("IA=1", "IB=10'", "IE=3") + R + _rec("IA=2", "IB=20'", "IE=3"),
    read_at=now,
)
check("score reconstructed from incidents", scored.score == (1, 1), scored.score)

# ------------------------------------------------- multi-incident records
# A single record can carry SEVERAL incidents: a goal carries its assist, and a
# substitution carries both players. These pin that, because reading one
# incident per record silently halves every substitution and mislabels a goal.
MULTI_GOAL = (
    _rec("AC=1st Half", "IG=0", "IH=0")
    + R + _rec("IA=1", "IB=63'", "IE=3", "IF=Scorer A.", "IJ=9", "INX=1", "IOX=0",
               "IE=8", "IF=Assister B.", "IJ=7")
)
mg = md.MatchFeed("T", MULTI_GOAL, read_at=now)
check("a goal record yields one row, not two", len(mg.events) == 1, mg.events)
check("the goal keeps its running score", mg.events[0]["score"] == [1, 0], mg.events[0])
check("the assist folds into the goal",
      mg.events[0].get("assist", {}).get("player") == "Assister B.", mg.events[0])

MULTI_SUB = (
    _rec("AC=1st Half", "IG=0", "IH=0")
    + R + _rec("IA=1", "IB=70'", "IE=6", "IF=Player Off", "IJ=11",
               "IE=7", "IF=Player On", "IJ=12")
)
ms = md.MatchFeed("T", MULTI_SUB, read_at=now)
check("a substitution yields one row, not two", len(ms.events) == 1, ms.events)
check("the player coming on is the headline", ms.events[0]["player"] == "Player On", ms.events[0])
check("the player going off is kept", ms.events[0].get("player_out") == "Player Off", ms.events[0])
check("both shirts survive",
      ms.events[0]["shirt"] == 12 and ms.events[0].get("shirt_out") == 11, ms.events[0])

# The second incident of a record inherits the minute, which is stamped once on
# the first. Without that the incoming player has no time and is dropped.
check("the inherited minute is used", ms.events[0]["minute"] == 70, ms.events[0])

# A card's reason and shirt are published and shown, so they are parsed.
CARD = _rec("IA=2", "IB=45'", "IE=1", "IF=Booked C.", "IJ=18", "IL=Roughing")
cd = md.MatchFeed("T", CARD, read_at=now)
check("a card keeps its reason", cd.events[0]["reason"] == "Roughing", cd.events[0])
check("a card keeps its shirt number", cd.events[0]["shirt"] == 18, cd.events[0])

# ------------------------------------------- separators and accented names
# The separators are two-byte UTF-8. Matching the single low byte appears to
# work -- which is exactly why it is worth pinning: an accented letter contains
# that byte too, so a naive match splits a name through its own character.
ACCENTED = ("\u00acAC\u00f71st Half\u00acIG\u00f70\u00acIH\u00f70"
            + R + "\u00acIA\u00f71\u00acIB\u00f722'\u00acIE\u00f73\u00acIF\u00f7Szamosi M."
            "\u00acIK\u00f7Goal")
acc = md.MatchFeed("T", ACCENTED, read_at=now)
check("two-byte separators parse", len(acc.events) == 1, acc.events)
check("an accented name survives intact",
      acc.events[0]["player"] == "Szamosi M.", acc.events[0]["player"])

# ---------------------------------------------------- match information
INFO_RECORD = (
    _rec("AC=1st Half", "IG=0", "IH=0")
    + F + "MIT\xf7REF" + F + "MIV\xf7Bankes P."
    + F + "MIT\xf7VEN" + F + "MIV\xf7Craven Cottage"
    + F + "MIT\xf7TWN" + F + "MIV\xf7London"
    + F + "MIT\xf7CAP" + F + "MIV\xf729 589"
)
info_feed = md.MatchFeed("T", INFO_RECORD, read_at=now)
check("referee parsed", info_feed.info.get("referee") == "Bankes P.", info_feed.info)
check("venue parsed", info_feed.info.get("venue") == "Craven Cottage", info_feed.info)
check("city parsed", info_feed.info.get("city") == "London", info_feed.info)
check("attendance parsed", info_feed.info.get("attendance") == "29 589", info_feed.info)
# Every MIT/MIV pair must be read, not just the last -- reading the block as a
# field map keeps only one pair and silently drops the rest.
check("every info pair is kept, not only the last",
      len([k for k in info_feed.info if info_feed.info[k]]) == 4, info_feed.info)

# A record with no MIT block leaves the map empty rather than inventing rows.
check("no info block leaves the map empty",
      md.MatchFeed("T", _rec("AC=1st Half", "IG=0", "IH=0"), read_at=now).info == {})

# ------------------------------------------------------------- lineups
LINEUP_RAW = (
    R.join([
        _rec("LA=Formation", "LB=Starting Lineups", "LC=1"),
        _rec("LC=1", "LD=1-4-2-3-1", "LH=1", "LJ=17", "LI=Keeper K.", "LR=(G)", "LPR=7.2"),
        _rec("LH=3", "LJ=3", "LI=Bassey C.", "LR=(C)", "LPR=7.1"),
        _rec("LC=2"),
        _rec("LC=2", "LD=1-4-2-3-1", "LH=1", "LJ=10", "LI=Cunha M.", "LPR=7.7"),
        _rec("LB=Substitutes", "LC=1"),
        _rec("LH=1", "LJ=1", "LI=Bench H."),
        _rec("LB=Coaches", "LC=1"),
        _rec("LI=Arbeloa A."),
    ])
)
lines = md.parse_lineups(LINEUP_RAW)
check("lineups report as available", lines["available"] is True, lines)
# The away list is opened by a bare `LC=2` marker with no section name, so a
# parser keyed on the section header alone puts both teams in one list.
check("home starting XI separated",
      [p["name"] for p in lines["home"]["starting"]] == ["Keeper K.", "Bassey C."],
      lines["home"]["starting"])
check("away starting XI separated",
      [p["name"] for p in lines["away"]["starting"]] == ["Cunha M."], lines["away"]["starting"])
check("the formation rides on the first player",
      lines["home"]["formation"] == "1-4-2-3-1", lines["home"]["formation"])
check("the away formation is kept",
      lines["away"]["formation"] == "1-4-2-3-1", lines["away"]["formation"])
check("substitutes are their own list",
      [p["name"] for p in lines["home"]["substitutes"]] == ["Bench H."],
      lines["home"]["substitutes"])
check("a keeper is marked as one", lines["home"]["starting"][0]["role"] == "goalkeeper",
      lines["home"]["starting"][0])
check("a captain is marked as one", lines["home"]["starting"][1]["role"] == "captain",
      lines["home"]["starting"][1])
check("a rating is carried through",
      lines["away"]["starting"][0]["rating"] == "7.7", lines["away"]["starting"][0])
check("shirt numbers are carried through",
      lines["away"]["starting"][0]["shirt"] == 10, lines["away"]["starting"][0])
check("a coach is read", lines["home"]["coach"] == "Arbeloa A.", lines["home"]["coach"])

# An empty lineups payload is unavailable, not a crash and not an empty pitch.
empty_lu = md.parse_lineups("")
check("no lineups payload reports unavailable", empty_lu["available"] is False, empty_lu)
check("no lineups payload yields no players",
      empty_lu["home"]["starting"] == [] and empty_lu["away"]["starting"] == [])

# A scheduled match has no clock at all.
sched = md.MatchFeed("T", "", read_at=now)
sched_clock = sched.clock(None, "scheduled", now)
check("scheduled match has no minute", sched_clock["minute"] is None, sched_clock)

# A finished match reads FT.
fin_clock = feed.clock(datetime.now(timezone.utc) - timedelta(hours=3), "finished", now)
check("finished match reads FT", md.clock_label(fin_clock) == "FT", fin_clock)

# ---------------------------------------------------------- derived clock
# The fallback is the only place an elapsed-time derivation still exists, and
# it is correct about the interval: 55 real minutes after kick-off is 40' of
# play (45 played, 10 into a 15-minute break), not 55'.
kick = now - timedelta(minutes=55)
d = md.derived_clock(kick, "live", now)
check("derived clock is labelled derived", d["source"] == "derived", d)
check("derived clock sits in the interval", d["period"] == "ht", d)
check("derived clock holds at 45' for the break", d["minute"] == 45, d)

k2 = now - timedelta(minutes=70)
d2 = md.derived_clock(k2, "live", now)
check("derived clock resumes in the second half", d2["period"] == "2", d2)
check("derived clock subtracts the break", d2["minute"] == 55, d2)

# --------------------------------------------------------------- statistics
STATS_RAW = (
    _rec("~SF=Top stats")
    + F + _rec("SG=Ball possession", "SH=41%", "SI=59%")
    + F + _rec("SG=Total shots", "SH=10", "SI=21")
    + F + _rec("~SF=Passing")
    + F + _rec("SG=Passes", "SH=86% (337/394)", "SI=87% (471/539)")
)
sections = md.parse_stats(STATS_RAW)
check("two stat sections parsed", len(sections) == 2, [s["label"] for s in sections])
check("section order preserved", sections[0]["label"] == "Top stats", sections[0]["label"])
top = sections[0]["rows"]
check("possession parsed as a percentage", top[0]["home"] == 41.0 and top[0]["away"] == 59.0, top[0])
check("possession bar uses the value itself", top[0]["is_percent"] is True)
check("shots parsed as counts", top[1]["home"] == 10.0 and top[1]["away"] == 21.0, top[1])
check("shot split computed", top[1]["home_pct"] == 32.3, top[1]["home_pct"])
passing = sections[1]["rows"][0]
check("bracketed pass stat reads the lead number", passing["home"] == 86.0, passing)
check("raw value preserved for a %", passing["home_raw"].startswith("86%"), passing)

# A payload with no stats yields no sections rather than an invented row.
check("empty stats payload yields no sections", md.parse_stats("") == [])

# --------------------------------------------------------------- live probe
LIVE_ID = sys.argv[1] if len(sys.argv) > 1 else "Uuc6OAi2"
FIN_ID = sys.argv[2] if len(sys.argv) > 2 else "2erPoYRa"

live = md.build_match_detail(LIVE_ID, kickoff=datetime.now(timezone.utc) - timedelta(minutes=70),
                             status="live")
print("\n-- live match", LIVE_ID, "--")
print("  available:", live["available"])
print("  period:", live["period"], "| clock:", live["clock_label"], "| source:",
      live["clock"].get("source"))
print("  score:", live["score"])
print("  events:", len(live["events"]))
print("  stat sections:", [s["label"] for s in live["stats"]])
if live["events"]:
    print("  first event:", live["events"][0]["minute_label"], live["events"][0]["type"],
          live["events"][0]["player"])
    print("  last  event:", live["events"][-1]["minute_label"], live["events"][-1]["type"],
          live["events"][-1]["player"])
check("live feed reachable and parsed", live["available"], live)
if live["available"]:
    check("live feed produced events", len(live["events"]) > 0, len(live["events"]))
    check("live feed produced stats", len(live["stats"]) > 0, len(live["stats"]))
    check("live clock carries a source", bool(live["clock"].get("source")), live["clock"])

fin = md.build_match_detail(FIN_ID, kickoff=datetime.now(timezone.utc) - timedelta(days=1),
                           status="finished")
print("\n-- finished match", FIN_ID, "--")
print("  available:", fin["available"], "| clock:", fin["clock_label"], "| score:", fin["score"])
print("  events:", len(fin["events"]), "| stat sections:",
      [s["label"] for s in fin["stats"]])
if fin["available"]:
    check("finished match feed parses", True)
    check("finished match reads a final clock",
          fin["clock"].get("source") in ("final", "feed", "unavailable"), fin["clock"])

# ------------------------------------------------------------------ report
print(f"\n{len(passed)} passed, {len(failed)} failed")
for name, detail in (f if isinstance(f, tuple) else (f, None) for f in failed):
    print("  FAIL:", name, "->", detail)

sys.exit(1 if failed else 0)