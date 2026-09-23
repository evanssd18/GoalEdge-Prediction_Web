"""Flashscore fixture source — real matches, dates, kick-off times and results.

The app's seeded fixtures are simulated. This module pulls the **real** football
schedule for a given day from Flashscore's public day feed and normalises it into
a stable shape the rest of the app can consume, so the Results page grades actual
football rather than generated scorelines.

Feed format
-----------
A Flashscore day feed (``/x/feed/f_1_<day>_3_<lang>_1``) is a flat text blob of
``KEY÷value`` pairs joined by ``¬``, with records separated by ``~``:

* A competition header record carries ``ZA`` (labelled name, e.g.
  ``"ENGLAND: Premier League"``), ``ZY`` (country) and ``ZEE`` (competition id).
* Each following match record carries ``AA`` (match id), ``AD`` (kick-off as a
  Unix timestamp), ``FH``/``AF`` (home/away team), ``OA``/``OB`` (crest files),
  ``AB`` (status) and — once played — ``AG``/``AH`` (final goals).

Status codes (verified against live payloads, not guessed):

======
``AB``  meaning
======
``1``   scheduled
``2``   live
``3``   finished
======

Only ``AG``/``AH`` hold the real full-time score; ``AW``/``BW``/``AX``/``BX``
are team-ordering slots that are present on *unplayed* matches too, so reading
them as goals would invent 0-0 results. They are deliberately ignored.

The feed token is a request header the public endpoint itself expects. It is not
a credential and grants no account access.
"""
from __future__ import annotations

import re
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import settings

#: ``AB`` -> our internal status vocabulary (matches the Fixture.status column).
STATUS_MAP = {"1": "scheduled", "2": "live", "3": "finished"}


class FlashscoreUnavailable(RuntimeError):
    """Raised when the feed cannot be loaded; callers degrade gracefully."""


# --------------------------------------------------------------------------
# normalisation helpers
# --------------------------------------------------------------------------

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """ASCII, lower-case, hyphenated — the same slug style the seed uses."""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return _SLUG_STRIP.sub("-", value.lower().replace("'", "").replace(".", "")).strip("-")


def split_competition(label: str) -> tuple[str | None, str]:
    """Split a feed label ``"ENGLAND: Premier League"`` into (country, name).

    Feed labels always use ``COUNTRY: Competition``; a label with no colon is
    treated as an international competition with no country.
    """
    if ":" in label:
        country, _, name = label.partition(":")
        return country.strip() or None, name.strip() or label.strip()
    return None, label.strip()


# --------------------------------------------------------------------------
# payload model
# --------------------------------------------------------------------------

@dataclass
class FeedMatch:
    """One fixture as Flashscore describes it."""

    match_id: str
    competition_id: str
    competition: str
    country: str | None
    home_team: str
    away_team: str
    kickoff: datetime
    status: str
    home_goals: int | None = None
    away_goals: int | None = None
    home_logo: str | None = None
    away_logo: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def played(self) -> bool:
        return self.status == "finished" and self.home_goals is not None


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

def _fields(record: str) -> dict[str, str]:
    """Turn one ``K÷v¬K÷v`` record into a dict."""
    out: dict[str, str] = {}
    for pair in record.split("¬"):
        if "÷" in pair:
            key, _, value = pair.partition("÷")
            out[key] = value
    return out


def _as_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None

#: A football match is not 90 straight minutes: the first half runs to
#: about 45 plus stoppage, then a 15-minute interval, then the second half.
#: Modelling the interval is what keeps a derived clock honest -- a straight
#: elapsed count shows 58' during the break, which is simply wrong.
FIRST_HALF_MINUTES = 45
HALF_TIME_MINUTES = 15
SECOND_HALF_MINUTES = 45

#: How much real time may pass after kick-off before the feed's own
#: 'live' flag is treated as stale rather than believed.
#:
#: 90 minutes of play plus a 15-minute interval is 105 real minutes. Real
#: stoppage is 1-10 minutes, so a match is genuinely over by about 115 real
#: minutes. 105 + 10 = 115, and the bound is set a minute past that so a match
#: in its final minute of stoppage is still shown as live rather than cut off
#: one minute early. Past it the match is over -- the feed has simply not closed
#: the row -- and a clock still reading "90+11'" there reads as a bug.
#:
#: This bound is deliberately inside the old 120-minute figure: the gap between
#: them was exactly the window in which a finished match displayed "90+15'"
#: instead of full time, because `MAX_STOPPAGE_MINUTES` (15) is a ceiling no
#: referee reaches and the clock kept counting up to it before the match was
#: declared over. Real stoppage tops out around 10, so declaring full time at
#: 116 real minutes still never clips genuine added time.
STALE_AFTER_MINUTES = 116

#: The most stoppage time any surface will print, in minutes.
#:
#: Real stoppage is 1-10 minutes; 15 is already beyond anything a referee
#: plays. Without this bound the raw arithmetic printed "90+15'" during the
#: final stretch, which reads as a quarter of an hour of added time and is
#: simply not something that happens.
MAX_STOPPAGE_MINUTES = 15


def live_clock(kickoff: datetime | None, now: datetime | None = None) -> dict:
    # The single live-clock model. Every surface that shows an in-play minute
    # renders from this, so the livescore board and the match page cannot
    # disagree about what minute a match is in.
    #
    # Returns a dict rather than a string so callers can render it their own
    # way while still sharing the arithmetic:
    #   minute        -- the in-play minute, or None when not in play
    #   seconds       -- seconds within the current minute, 0-59
    #   added         -- stoppage minutes folded into the label (45+2)
    #   period        -- '1' | 'ht' | '2' | 'ft' | None
    #   period_label  -- human text for the period
    #   period_offset -- seconds to add to the within-period clock to get time
    #                    since kick-off (see below)
    #   running       -- whether the clock is actually ticking
    #   stale         -- the feed still says live but the match must be over
    #   source        -- always 'derived': the feed publishes no clock
    #
    # The `seconds` field is what lets the match page print MM:SS rather than a
    # bare minute. It is the *real* second offset, not a guess at how far a
    # particular match is into its current minute: this clock is derived from
    # kick-off, so second 0 is kick-off and the value advances once per second.
    #
    # `period_offset` exists because the clock must be continuous from kick-off.
    # First-half stoppage is played before the interval, so the second half has
    # to start from 45 + however long the first half actually ran. Without it,
    # each half's count restarts and the page would show 45:00 twice -- once at
    # the end of the first half and again at the start of the second -- and the
    # interval would be silently swallowed. With it, a match that saw 4 minutes
    # of first-half stoppage starts its second half at 49:00, so `period_offset
    # + seconds` is time since kick-off across the whole match.
    #
    # The feed carries NO in-play minute. AC (and its dc_1 twin DB) is a phase
    # code -- sampled across 21 live matches it held only the values 12, 13, 38
    # and 7, and not one of them moved over 75 seconds while real elapsed time
    # spanned 5' to 125'. MW is a bitmask and IB is the minute of the last
    # event, so it jumps only when someone scores. The clock is therefore
    # derived from kick-off, which the feed does report accurately, and every
    # surface labels it as derived rather than passing it off as a feed value.
    blank = {"minute": None, "seconds": 0, "added": 0, "period": None,
             "period_label": "", "period_offset": 0,
             "running": False, "stale": False, "source": "derived"}
    if kickoff is None:
        return blank
    now = now or datetime.now(timezone.utc)
    # SQLite hands back naive datetimes; the feed's own are aware UTC. Compare
    # like with like, or every stored row reads as thousands of minutes old.
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    # Real seconds, not floored minutes. Everything below is derived from this
    # one number, so the minute and the second within it always agree, and the
    # pair `period_offset + seconds` is a continuous count since kick-off.
    elapsed_s = int((now - kickoff).total_seconds())
    if elapsed_s < 0:
        return blank
    elapsed = elapsed_s // 60
    if elapsed > STALE_AFTER_MINUTES:
        # The feed never closed this one. Report it finished rather than
        # inventing a minute that no football match has.
        return {**blank, "period": "ft", "period_label": "Full-time",
                "stale": True, "source": "stale"}

    if elapsed_s < FIRST_HALF_MINUTES * 60:
        # Strictly inside the first half. Comparing seconds rather than floored
        # minutes keeps the boundary honest: at exactly 45:00.000 the 46th
        # minute has begun only if the half really runs long, and the interval
        # test below owns that case.
        return {"minute": elapsed, "seconds": elapsed_s % 60, "added": 0,
                "period": "1", "period_label": "1st half",
                "period_offset": 0, "running": elapsed_s > 0,
                "stale": False, "source": "derived"}

    if elapsed_s < (FIRST_HALF_MINUTES + HALF_TIME_MINUTES) * 60:
        # 45:00 up to the second half kicking off. The lower bound is EXCLUSIVE
        # of the 45:00 instant itself so that a clock sitting exactly on 45:00
        # reads as the first half (handled above) rather than as half-time.
        # In the interval. The minute stays pinned at the end of the first
        # half; only the stoppage count creeps, which is what a half-time
        # board shows. The seconds are held at 0 with it: a half-time board
        # reads "HT", and a pair that kept counting to 45:37 would suggest
        # football was still being played.
        return {"minute": FIRST_HALF_MINUTES, "seconds": 0,
                "added": max(0, elapsed - FIRST_HALF_MINUTES),
                "period": "ht", "period_label": "Half-time",
                "period_offset": 0,
                "running": False, "stale": False, "source": "derived"}

    # The second half. Real time since kick-off minus the interval IS the
    # match clock, so this is exact rather than merely plausible -- and it is
    # what makes the clock continuous: first-half stoppage is already inside
    # `since_kickoff`, so it carries into the second half automatically.
    since_kickoff = elapsed_s - HALF_TIME_MINUTES * 60
    played = since_kickoff // 60
    if played <= 90:
        return {"minute": played, "seconds": since_kickoff % 60, "added": 0,
                "period": "2", "period_label": "2nd half",
                "period_offset": HALF_TIME_MINUTES * 60, "running": True,
                "stale": False, "source": "derived"}

    # Past 90' of play but still inside the real-time bound: stoppage time,
    # which is genuine. It reads 90+N rather than climbing to 91, 92, 93, and
    # N is capped at MAX_STOPPAGE_MINUTES so the label cannot grow into a
    # number no referee plays. The seconds keep running through it, so the
    # countdown to the final whistle is visible rather than frozen.
    return {"minute": 90, "seconds": since_kickoff % 60,
            "added": min(played - 90, MAX_STOPPAGE_MINUTES),
            "period": "2", "period_label": "2nd half", "running": True,
            "period_offset": HALF_TIME_MINUTES * 60,
            "stale": False, "source": "derived"}


def live_minute_label(kickoff: datetime | None, now: datetime | None = None) -> str | None:
    # The board's one-line form of live_clock: 63', 45+2', HT, or None.
    #
    # Rendered from the shared model rather than counting minutes here, so the
    # board and the match page cannot drift apart. Returns None for a match
    # that is not in play -- including one the model has called finished.
    clock = live_clock(kickoff, now)
    if clock.get("period") == "ft":
        return None
    minute = clock.get("minute")
    if minute is None:
        return None
    period = clock.get("period")
    if period == "ht":
        return "HT"
    added = clock.get("added") or 0
    return f"{minute}+{added}'" if added else f"{minute}'"

def _live_label(f: dict[str, str], kickoff: datetime | None = None) -> str | None:
    """The in-play minute label (e.g. 63'), or None for a match not in play.

    The feed publishes no clock. ``AC`` (and its ``dc_1`` twin ``DB``) is a
    phase code: it reads 13 for most in-play matches and 12 for others, keyed to
    the kick-off slot rather than to elapsed time. Sampled over 82 seconds it
    never moved, while the score in the same payload advanced from 2-0 to 3-0.
    Reading it as a minute is what put a flat "13'" beside every live row --
    the bug this replaces. ``MW`` is a bitmask of event/bookmaker codes
    ("417|1271|49|657"), not a minute, and ``IB`` in ``df_sui_1`` is the minute
    of the last recorded *event*, so it would jump only when someone scores.

    The clock is therefore derived from kick-off, which the feed does report
    accurately. It is labelled as derived in the API so it is never presented as
    a feed value. It is bounded at 120' (extra time is real, so 90' is not the
    ceiling) and past that the feed is treated as stale.
    """
    if STATUS_MAP.get(f.get("AB", ""), "scheduled") != "live":
        return None
    return live_minute_label(kickoff)


def _crest(name: str | None) -> str | None:
    """Flashscore ships crest *filenames*; build the public image URL."""
    if not name:
        return None
    if name.startswith("http"):
        return name
    return f"https://static.flashscore.com/res/image/data/{name}"


def parse_day_feed(raw: str) -> list[FeedMatch]:
    """Parse a raw day feed into normalised matches.

    Competition headers set the context for the match records that follow, so
    this walks the payload in order rather than treating it as a flat list.
    """
    matches: list[FeedMatch] = []
    current_label: str | None = None
    current_comp_id: str | None = None
    current_country: str | None = None

    for record in raw.split("~"):
        record = record.strip()
        if not record:
            continue
        f = _fields(record)

        if "ZA" in f:
            current_label = f["ZA"]
            current_comp_id = f.get("ZEE") or slugify(current_label)
            country, _ = split_competition(current_label)
            current_country = f.get("ZY") or country
            continue

        if "AA" not in f or "AD" not in f:
            continue

        kickoff_ts = _as_int(f.get("AD"))
        if kickoff_ts is None:
            continue

        home = (f.get("FH") or f.get("AE") or "").strip()
        away = (f.get("AF") or "").strip()
        if not home or not away:
            continue

        status = STATUS_MAP.get(f.get("AB", ""), "scheduled")
        home_goals = _as_int(f.get("AG"))
        away_goals = _as_int(f.get("AH"))

        # A finished match with no score line is not usable: better to drop it
        # than to record a guessed 0-0.
        if status == "finished" and (home_goals is None or away_goals is None):
            status = "scheduled"

        label = current_label or "Unknown competition"
        _, comp_name = split_competition(label)

        # An in-play score is a real score and is kept: a live board that shows
        # 0-0 for a match already at 2-1 is worse than showing nothing. Only a
        # *scheduled* match has genuinely no score yet.
        has_score = status in ("live", "finished")

        kickoff = datetime.fromtimestamp(kickoff_ts, tz=timezone.utc)

        matches.append(
            FeedMatch(
                match_id=f["AA"],
                competition_id=current_comp_id or slugify(label),
                competition=comp_name,
                country=current_country,
                home_team=home,
                away_team=away,
                kickoff=kickoff,
                status=status,
                home_goals=home_goals if has_score else None,
                away_goals=away_goals if has_score else None,
                home_logo=_crest(f.get("OA")),
                away_logo=_crest(f.get("OB")),
                extra={
                    "feed_label": label,
                    "live_label": _live_label(f, kickoff),
                    "half_home": _as_int(f.get("AS")),
                    "half_away": _as_int(f.get("AT")),
                },
            )
        )

    return matches


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

#: The eight major European competitions the site treats as "top leagues".
#:
#: Matched on the feed's own COUNTRY + COMPETITION name rather than on a stored
#: slug, because the sync appends the feed's competition hash to the slug it
#: builds (``england-premier-league-dylosqod``) and that hash is not stable
#: across feed responses. A senior league is matched exactly; the qualifier test
#: in :func:`is_top_league` keeps the women's, youth, second-tier and cup
#: variants of the same name out.
TOP_LEAGUE_KEYS: tuple[tuple[str, str], ...] = (
    ("England", "premier league"),
    ("Spain", "laliga"),
    ("Italy", "serie a"),
    ("Germany", "bundesliga"),
    ("France", "ligue 1"),
    ("Netherlands", "eredivisie"),
    ("Europe", "champions league"),
    ("Europe", "europa league"),
)

#: The two continental cups the feed names with a round suffix.
#:
#: Flashscore does not publish a bare "Champions League" / "Europa League" any
#: more: the competition is "Champions League - League phase" (and the same for
#: the Europa League). A tail test that rejects every ``league phase`` therefore
#: rejected the senior tournament itself, so neither cup could ever be pinned as
#: a top league -- which is exactly why the sidebar and the "Top leagues" filter
#: never offered them.
#:
#: A round name is not the same kind of qualifier as "Women" or "Qualification":
#: it names a stage *of this competition*, not a different competition that
#: shares its name. So for the two cups the round tail is accepted, and the
#: qualifiers that still mark a DIFFERENT competition -- women's, youth, and the
#: pre-tournament "qualification"/"play offs" rounds -- are rejected.
_EURO_CUP_ROUND = re.compile(r"^\s*-?\s*(league\s*phase|group\s*stage|group\s*[a-h])\s*$")

#: A qualifier that marks a competition as NOT the senior cup it is named after.
#: Deliberately excludes "league phase"/"group stage": those name a round of the
#: senior tournament, whereas these name a different tournament entirely.
_EURO_CUP_EXCLUDE = re.compile(
    r"^(\s*-?\s*)?(women|women's|youth|junior|reserve|academy|"
    r"qualification|qualifiers?|play[ -]?offs?|preliminary|"
    r"\d+(\.\s|\s)|u-?\d|19|21)\b"
)

#: A qualifier either side of the league name marks a competition as NOT the
#: senior league it is named after. "Premier League 2" is the U21 division,
#: "LaLiga2" is the second tier, "2. Bundesliga" is the German second division,
#: "Serie A Women" is the women's league, and "Champions League - League phase"
#: is a preliminary round. Pinning any of those as a top league would put a
#: reserve or second-tier fixture in the marquee block, so the test is strict.
_TAIL_QUALIFIERS = re.compile(
    r"(women|youth|junior|reserve|academy|cup|qualification|playoffs?"
    r"|league_?phase|\bii\b|u-?\d|\d)"
)
#: A leading tier marker: "2. Bundesliga", "1. Liga". Checked separately because
#: it sits BEFORE the league name, where a tail-only test never looks.
_HEAD_QUALIFIERS = re.compile(r"^\s*\d+\.?\s+")


def is_top_league(country: str | None, competition: str | None) -> bool:
    """Whether a competition is one of the eight senior top leagues.

    ``country``/``competition`` are the feed's own fields, not a database
    competition row.

    The test is split around the league name, because a qualifier can sit on
    either side and a single blanket test is wrong in both directions:

    * Testing the whole name rejects the senior league itself -- "Ligue 1" and
      "Serie A" both contain a digit, so a plain digit-test would throw them out.
    * Testing only the tail misses "2. Bundesliga", where the second-tier marker
      is a *prefix*.

    So the head is checked for a leading tier number and the tail for every
    age, gender, cup or phase marker. "LaLiga**2**" is rejected on its tail while
    "Ligue **1**" passes, because the digit there is part of the name, not after
    it.
    """
    if not country or not competition:
        return False
    country_l = country.strip().lower()
    comp = competition.strip().lower()
    if _HEAD_QUALIFIERS.match(comp):
        return False
    for want_country, want_comp in TOP_LEAGUE_KEYS:
        if country_l != want_country.lower():
            continue
        # Compare with spaces removed so "La Liga" and "LaLiga" both match.
        needle = want_comp.replace(" ", "")
        hay = comp.replace(" ", "")
        at = hay.find(needle)
        if at == -1:
            continue
        tail = hay[at + len(needle):]
        if want_country == "Europe":
            # The continental cups carry a round name in the tail ("- League
            # phase"), which names a stage of THIS competition -- so it is
            # accepted. A women's, youth or qualification marker still means a
            # different tournament and is rejected.
            if _EURO_CUP_EXCLUDE.search(tail):
                continue
            if not tail or _EURO_CUP_ROUND.match(tail):
                return True
            continue
        if not _TAIL_QUALIFIERS.search(tail):
            return True
    return False


def _fetch(path: str) -> str:
    base = settings.flashscore_base_url.rstrip("/")
    request = urllib.request.Request(
        f"{base}{path}",
        headers={
            "x-fsign": settings.flashscore_feed_token,
            "Referer": f"{base}/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "*/*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.flashscore_timeout_seconds) as resp:
            return resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FlashscoreUnavailable(f"Flashscore request failed: {exc}") from exc


def day_offset(value: str | int) -> int:
    """Translate 'today'/'tomorrow'/'yesterday'/``YYYY-MM-DD`` to a feed offset.

    The day feed addresses days relative to the *site's* current day (0 = today,
    1 = tomorrow, -1 = yesterday). An explicit ``YYYY-MM-DD`` is converted to the
    matching offset so a caller can ask for a specific calendar day.
    """
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    if text in ("today", ""):
        return 0
    if text == "tomorrow":
        return 1
    if text == "yesterday":
        return -1
    try:
        target = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(
            "date must be today, tomorrow, yesterday or YYYY-MM-DD"
        ) from exc
    return (target - datetime.now(timezone.utc).date()).days


def fetch_matches_for_day(date: str | int = "today") -> list[FeedMatch]:
    """Real matches for one day, in kick-off order.

    ``date`` accepts today / tomorrow / yesterday / ``YYYY-MM-DD`` or a raw
    feed offset. Raises :class:`FlashscoreUnavailable` if the feed is down.

    Two feed *variants* are merged, because neither is complete on its own. The
    ``_3_`` variant is the one this app has always read; the ``_2_`` variant is
    broader and is the only one that carries the major European leagues -- on a
    day with Premier League, LaLiga, Serie A, Bundesliga and Ligue 1 fixtures in
    play, ``_3_`` returned none of them while ``_2_`` returned all five. Reading
    either alone therefore silently omits real football: ``_3_`` hides the top
    leagues, ``_2_`` is thinner on the rest of the world.

    Merging is by the feed's own match id (``AA``), so a match appearing in both
    variants -- most do -- is counted once. The ``_3_`` entry wins on a clash
    because it is the variant the app's other fields were validated against.
    ``_2_`` failing is not fatal: the day is still served from ``_3_`` rather
    than the whole board erroring because one variant was down.
    """
    offset = day_offset(date)
    lang = settings.flashscore_lang
    sport = settings.flashscore_sport_id
    matches: list[FeedMatch] = []
    seen: set[str] = set()
    failure: FlashscoreUnavailable | None = None
    # Primary first: on a duplicate id its record is the one kept.
    for variant in (3, 2):
        path = f"/x/feed/f_{sport}_{offset}_{variant}_{lang}_1"
        try:
            raw = _fetch(path)
        except FlashscoreUnavailable as exc:
            # The primary must succeed or the caller has nothing to serve; the
            # secondary is best-effort and its absence only narrows the day.
            if variant == 3:
                failure = exc
            continue
        for match in parse_day_feed(raw):
            if match.match_id in seen:
                continue
            seen.add(match.match_id)
            matches.append(match)


    if failure is not None and not matches:
        raise failure
    matches.sort(key=lambda m: m.kickoff)
    return _cap_matches(matches, settings.flashscore_max_matches_per_day)


def _cap_matches(matches: list[FeedMatch], cap: int) -> list[FeedMatch]:
    """Trim a day to ``cap`` matches without dropping whole competitions.

    A plain kick-off-ordered slice is what silently removed the top leagues: the
    merged day carries well over a thousand matches, so the cut fell in the
    middle of the afternoon and every later fixture -- which is when the European
    leagues play -- disappeared. Sorting by kick-off is right for *reading*, but
    it is the wrong order for *cutting*.

    So the cap is filled in two passes: every top-league fixture first, then the
    remainder in kick-off order. This keeps the marquee block intact while still
    bounding what the day costs to store and render. A ``cap`` smaller than the
    top-league count is honoured exactly -- the caller's bound is not exceeded.
    """
    if len(matches) <= cap:
        return matches
    top = [m for m in matches if is_top_league(m.country, m.competition)]
    rest = [m for m in matches if not is_top_league(m.country, m.competition)]
    kept = top[:cap]
    if len(kept) < cap:
        kept.extend(rest[: cap - len(kept)])
    kept.sort(key=lambda m: m.kickoff)
    return kept
