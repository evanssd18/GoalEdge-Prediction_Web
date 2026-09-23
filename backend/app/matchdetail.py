"""Real match-page data: the minute, the score, the statistics, the events.

The match page (opened by clicking a row on the Livescore board) needs what the
board deliberately does not carry: **the minute the match is in**, its running
score, the events that produced it, and the statistics behind it.

Where that minute comes from is the whole point of this module. The day feed
publishes no running clock -- ``AC`` there is a phase code, ``AO`` a
last-state-change timestamp, ``IB`` the minute of the last recorded *event*.
The per-match **summary feed** (``df_sui_1_<mid>``) however does: it names the
period (``AC`` = "1st Half"/"2nd Half") and carries every incident with the
minute it happened on (``IB`` = "5'", "45+1'"). The clock therefore rests on
two real, published facts instead of a guess:

1. **A clock anchor** -- the last published event minute, plus the real time
   elapsed since that event arrived. That is what makes the displayed clock
   advance minute by minute between events rather than sitting still until the
   next goal.
2. **The interval plan** -- modelled when nothing is published, so a match in
   the break reads 45' rather than the 58' a naive elapsed count would show.

The payload says which source it used under ``clock.source`` -- ``"feed"`` when
a minute was published, ``"derived"`` when it had to be reconstructed -- so the
UI never presents a derived number as a measured one. That is the same contract
the livescore board keeps.

The field vocabulary below was **observed**, not assumed: ``qa/probe-match-feed.py``
dumps every key the live payload actually sends for a real match id.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import flashscore as fs

# The three separators, in the form the feed ACTUALLY sends them.
#
# Each is a two-byte UTF-8 sequence: the payload encodes the separator
# characters themselves rather than emitting the raw bytes. Matching the single
# low byte ("\xac") appears to work, and is a real bug hiding behind a lucky
# first byte: "\xc2\xac" and "\xc3\xb7" CONTAIN that byte, but so does any
# accented letter -- an "é" is "\xc3\xa9", and a name containing one can be
# split through the middle of its own character. That is precisely how a player
# name arrives blank while the rest of the record parses cleanly.
_FIELD = "\u00ac"    # "not sign" -- separates fields inside a record
_KV = "\u00f7"       # division sign -- separates a key from its value
_RECORD = "\u007e"   # tilde -- separates one record from the next

#: The incident type codes the summary feed sends in its `IE` field, mapped to
#: a stable kind the UI can give an icon and a colour to.
#:
#: `IE` is the authority and `IK` is NOT: a goal arrives with `IE=3` and *no*
#: `IK` at all, while cards and substitutions carry both. Keying off `IK` alone
#: therefore silently dropped every goal -- a timeline with the cards but not
#: the score in it. The codes below are the ones a survey of real payloads
#: actually returned, not a guess at their meaning.
_IE_KINDS = {
    "1": "yellow",
    "2": "red",
    "3": "goal",
    "4": "own-goal",
    "5": "note",       # "Penalty Awarded" -- the decision, not the kick
    "6": "sub-off",   # the player going off
    "7": "sub-on",    # the player coming on
    "8": "assist",
    "9": "var",
    "10": "penalty",
    "11": "miss",
    "47": "note",
}

#: The player going off, and the player coming on. The two arrive as consecutive
#: incidents sharing one record, and are folded into a single row. The codes were
#: read off the payload rather than inferred from the numbers: 6 is
#: "Substitution - Out" and 7 is "Substitution - In" -- the opposite of what the
#: digits suggest, which is exactly the kind of guess that puts the wrong name on
#: the pitch.
_SUB_OFF_CODES = {"6"}
_SUB_ON_CODES = {"7"}
#: The human name for each code, used as the event's label when `IK` is absent.
_IE_LABELS = {
    "1": "Yellow card",
    "2": "Red card",
    "3": "Goal",
    "4": "Own goal",
    "6": "Substitution",
    "7": "Substitution",
    "8": "Assist",
    "9": "VAR",
    "10": "Penalty",
    "11": "Missed penalty",
}

#: Codes that are part of another incident rather than incidents of their own.
#: An assist rides on the goal record above it, so it folds into its parent
#: instead of becoming an extra row -- otherwise every goal is listed twice.
_IE_CHILD_KINDS = {"assist"}

#: Kinds that mean the score moved, used to reconstruct a score the feed did
#: not pair with a period header.
_GOAL_KINDS = {"goal", "own-goal", "penalty"}


def _fields(record: str) -> dict[str, str]:
    """One ``¬``-separated record into a ``{key: value}`` map."""
    out: dict[str, str] = {}
    for pair in record.split(_FIELD):
        key, _, value = pair.partition(_KV)
        if key:
            out[key] = value
    return out


def _as_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_minute(text: str | None) -> tuple[int, int] | None:
    """``"45+1'"`` -> ``(45, 1)``; ``"63'"`` -> ``(63, 0)``."""
    if not text:
        return None
    cleaned = text.strip().rstrip("'").strip()
    if not cleaned:
        return None
    base, _, added = cleaned.partition("+")
    minute = _as_int(base)
    if minute is None:
        return None
    return minute, (_as_int(added) or 0)


def _sort_key(parts: tuple[int, int]) -> int:
    return parts[0] * 10 + parts[1]

def _split_incidents(record: str) -> list[dict[str, str]]:
    """Split one feed record into its incidents.

    A record can carry MORE THAN ONE incident. The feed repeats the whole
    `IE`/`IA`/`IB`/`IF`/`IK` group inside a single record: a goal record also
    carries the assist, and a substitution record carries both the player coming
    on and the one going off. Reading one incident per record therefore loses
    half of every substitution and mislabels a goal as an assist -- so the
    record is split on each repeated `IE`, and every group becomes its own
    incident.

    Fields before the first `IE` belong to no incident (a record id, the score
    header) and are attached to the first group, which is where the feed puts
    the shared context.
    """
    groups: list[dict[str, str]] = []
    shared: dict[str, str] = {}
    current: dict[str, str] | None = None
    for pair in record.split(_FIELD):
        key, _, value = pair.partition(_KV)
        if not key:
            continue
        if key == "IE":
            if current is not None:
                groups.append(current)
            current = {}
            current[key] = value
        elif current is None:
            # Before the first `IE`: this is the record's own context, and every
            # incident in the record inherits it. Critically that includes `IB`,
            # the minute -- a substitution stamps the minute ONCE, on its first
            # half, so an incident read without it has no time and is dropped.
            shared[key] = value
        else:
            current[key] = value
    if current is not None:
        groups.append(current)
    # The shared context is merged UNDER each group, so a key an incident sets
    # itself (its own `IF`, `IK`, `IJ`) wins over the record-level default.
    return [{**shared, **g} for g in groups]
#: The match-information fields, as the `MIT`/`MIV` block names them: a
#: reference number, its kind, the referee, the venue and city, the attendance
#: and the round. These are the rows the reference page lists under its own
#: "Match information" heading, so they are carried as written rather than
#: interpreted -- the numbers are ids and counts, not statistics to be derived.
_INFO_FIELDS = {
    "REF": "referee",
    "VEN": "venue",
    "TWN": "city",
    "CAP": "attendance",
    "RCO": "round_code",
    "RTY": "round_type",
    "RCC": "round_country",
    "STY": "stage_type",
    "SCO": "stage_code",
}


def record_info(record: str, into: dict) -> None:
    # Merge the match-information block into the info map.
    #
    # The block is a run of repeated `MIT`/`MIV` PAIRS inside one record --
    # `MIT=REF` then `MIV=Bankes P.` then `MIT=VEN` then `MIV=Craven Cottage`.
    # Reading it as a field map keeps only the last pair, so it is walked in
    # order with the pending name carried forward, the same way the incident
    # groups are read.
    pending: str | None = None
    for pair in record.split(_FIELD):
        key, _, value = pair.partition(_KV)
        if key == "MIT":
            pending = _INFO_FIELDS.get(value.strip())
        elif key == "MIV" and pending:
            value = value.strip()
            if value:
                into[pending] = value
            pending = None


class MatchFeed:
    """The summary feed for one match, parsed once and read from many ways.

    Fetching is separated from parsing so the parser can be tested against a
    captured payload with no network at all -- the same split the day feed
    uses, and the reason a feed shape change becomes a test failure rather than
    a broken page.
    """

    def __init__(self, match_id: str, raw: str = "", read_at: datetime | None = None) -> None:
        self.match_id = match_id
        self.raw = raw
        self.period: str | None = None       # "1st Half" / "2nd Half"
        self.score = (0, 0)
        self.events: list[dict] = []
        #: Referee, venue, city, attendance and the round, as the feed reports
        #: them. Empty when the block is absent rather than guessed at.
        self.info: dict[str, str] = {}
        self._latest_minute = 0
        self._latest_added = 0
        #: When the payload was read. The clock counts up from this instant, so
        #: a caller replaying a captured payload can pin it.
        self.read_at = read_at or datetime.now(timezone.utc)

        if raw:
            self._parse()

    # ------------------------------------------------------------------ parse
    def _parse(self) -> None:
        # Records arrive grouped per period: a block opens with `AC` (the
        # period's name) and `IG`/`IH` (the score), then that period's incidents
        # follow. Walking in order is what keeps each incident under the right
        # half, so "1st Half" shows only first-half events.
        current_period: str | None = None
        for record in self.raw.split(_RECORD):
            fields = _fields(record)
            if not fields:
                continue
            # The match-information block (`MIT`/`MIV`) rides on the first
            # record alongside the period header, so it is read here rather than
            # in a pass of its own -- there. is only ever one of them.
            if "MIT" in record:
                record_info(record, self.info)

            # A period header. `~AC` opens the *next* block, plain `AC` names
            # the first one, so both are read the same way.
            label = (fields.get("~AC") or fields.get("AC") or "").strip()
            if label:
                current_period = label
                self.period = label
                home = _as_int(fields.get("IG"))
                away = _as_int(fields.get("IH"))
                if home is not None and away is not None:
                    self.score = (home, away)
                continue
            # One record can hold SEVERAL incidents (a goal carries its assist,
            # a substitution both players), so it is split rather than read once.
            for incident in _split_incidents(record):
                self._add_incident(incident, current_period)

        # Events come off the wire in the order they happened; sorting makes
        # that explicit rather than trusting the wire, and is stable so two
        # events stamped the same minute keep their order.
        self.events.sort(key=lambda e: (e["minute"], e["added"]))
        self._merge_substitutions()

    def _add_incident(self, fields: dict[str, str], period: str | None) -> None:
        # An incident is identified by `IE` (its type code) and `IB` (the minute
        # it happened on -- published, not derived). Both are required: an
        # incident with a minute but no `IE` is a stray record, and one with an
        # `IE` but no minute has not happened yet.
        code = (fields.get("IE") or "").strip()
        parts = _parse_minute(fields.get("IB"))
        if not code or parts is None:
            return
        kind = _IE_KINDS.get(code, "note")
        detail = (fields.get("IK") or "").strip()
        if detail.lower() == "own goal":
            kind = "own-goal"

        # An assist is part of the goal above it, not a row of its own -- the
        # reference page shows it as a sub-line under the scorer.
        if kind in _IE_CHILD_KINDS and self.events:
            parent = self.events[-1]
            if parent.get("minute") == parts[0] and parent["kind"] in _GOAL_KINDS:
                parent["assist"] = {
                    "player": (fields.get("IF") or "").strip() or None,
                    "player_id": (fields.get("IM") or "").strip() or None,
                }
                return
        side = _as_int(fields.get("IA"))
        # The score *after* this incident, which the feed publishes per goal and
        # is what the reference shows beside it ("1:0"). Absent on non-scoring
        # events, so it stays null rather than being carried forward.
        running = None
        nx, ox = _as_int(fields.get("INX")), _as_int(fields.get("IOX"))
        if nx is not None and ox is not None:
            running = [nx, ox]

        self.events.append({
            "minute": parts[0],
            "added": parts[1],
            "minute_label": (fields.get("IB") or "").strip(),
            "kind": kind,
            "type": detail or _IE_LABELS.get(code, "Event"),
            "code": code,
            "team": "home" if side == 1 else "away" if side == 2 else None,
            "player": (fields.get("IF") or "").strip() or None,
            "player_id": (fields.get("IM") or "").strip() or None,
            "shirt": _as_int(fields.get("IJ")),
            "reason": (fields.get("IL") or "").strip() or None,
            "text": (fields.get("ICT") or "").strip() or None,
            "score": running,
            "period": period,
        })

        if _sort_key(parts) >= _sort_key((self._latest_minute, self._latest_added)):
            self._latest_minute, self._latest_added = parts
    def _merge_substitutions(self) -> None:
        # A substitution arrives as two incidents in one record -- the player
        # coming on and the one going off, in that order. They are the same
        # event to a reader, so they are folded into one row carrying both
        # names, which is how the reference page shows it.
        merged: list[dict] = []
        for event in self.events:
            previous = merged[-1] if merged else None
            # A pair is exactly `sub-off` then `sub-on`, same side, same minute.
            # Matching on "both are sub codes" alone paired a Penalty Awarded
            # with the penalty kick that followed it, because those codes sit
            # adjacent too -- so the direction is checked, not just the family.
            same_sub = (
                previous is not None
                and previous.get("code") in _SUB_OFF_CODES
                and event.get("code") in _SUB_ON_CODES
                and previous.get("team") == event.get("team")
                and previous.get("minute") == event.get("minute")
            )
            if not same_sub:
                merged.append(event)
                continue
            # Whichever order the halves arrived in, the player coming ON is the
            # headline and the one going off is the sub-line -- that is the order
            # the reference page reads in.
            off, on = (previous, event) if previous["code"] in _SUB_OFF_CODES else (event, previous)
            on = dict(on)
            on["kind"] = "sub"
            on["type"] = "Substitution"
            on["player_out"] = off.get("player")
            on["player_out_id"] = off.get("player_id")
            on["shirt_out"] = off.get("shirt")
            merged[-1] = on
        self.events = merged

        # The running score the feed publishes ON each goal (`INX`/`IOX`) is
        # fresher than the period header. The header (`AC` + `IG`/`IH`) is only
        # rewritten when the feed opens a new block, so a goal scored mid-period
        # leaves the header at the OLD scoreline while the goal event beside it
        # already carries the new one -- which is how a 2-1 read as 1-0 on the
        # header until the next block. So the latest published running score wins
        # whenever it is ahead of the header on either side.
        #
        # "Ahead" rather than "different": a stale or re-sent goal must never
        # walk the score BACKWARDS, and a running score is by construction the
        # score after that goal, so it can only move the pair forward.
        for e in self.events:
            running = e.get("score")
            if not running:
                continue
            if running[0] >= self.score[0] and running[1] >= self.score[1]:
                if running[0] > self.score[0] or running[1] > self.score[1]:
                    self.score = (running[0], running[1])

        # A score the feed never paired with a header is reconstructed from the
        # goals themselves -- home goals plus away own goals for one side, and
        # the mirror for the other. Without this a feed that only sends
        # incidents would show 0-0 for a match that is 2-1.
        if self.score == (0, 0) and self.events:
            home = away = 0
            for e in self.events:
                if e["kind"] not in _GOAL_KINDS:
                    continue
                # An own goal counts for the *other* side.
                scorer = e["team"]
                if e["kind"] == "own-goal":
                    scorer = "away" if scorer == "home" else "home" if scorer == "away" else None
                if scorer == "home":
                    home += 1
                elif scorer == "away":
                    away += 1
            if home or away:
                self.score = (home, away)

    @property
    def latest_event(self) -> dict | None:
        return self.events[-1] if self.events else None

    # ------------------------------------------------------------------ clock
    def clock(self, kickoff: datetime | None, status: str, now: datetime | None = None) -> dict:
        """The current clock: minute, seconds, added time, period, and how found.

        Two sources, in order of preference, and the reader is told which:

        * **derived** -- reconstructed from the published kick-off and counting
          up once per second. This is the preferred source and the default:
          kick-off is an exact published fact, so a match that started 34
          minutes ago is in its 35th minute whether or not anyone has scored.
          It is the same model the livescore board uses, so the two agree.
        * **feed** -- reachable only when there is no usable kick-off but the
          summary feed did publish an event minute. That minute anchors the
          clock. It is the weaker source and is labelled as such: its minute is
          that of the last recorded *incident*, so it stalls between events and
          can sit far behind reality. Used rather than showing nothing.
        """
        now = now or datetime.now(timezone.utc)
        if kickoff is not None and kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)

        if status != "live":
            return self._final_clock(status)

        # --- source 1: reconstructed from the published kick-off ----------
        # Preferred, and reachable for every fixture the app holds -- the clock
        # comes from a timestamp the feed publishes exactly, not from an
        # incident that may be an hour old.
        derived = derived_clock(kickoff, status, now)
        # A derived clock that says the match is OVER (full-time, or stale past
        # the realistic end) is returned as-is, before the minute check below.
        # Otherwise a stale-live match fell through to the incident-anchored
        # clock and the page showed the last event's minute for a match that had
        # finished -- the very "finished match showing a minute" this fixes.
        if derived.get("period") == "ft" or derived.get("stale"):
            return derived
        if derived.get("minute") is not None:
            if self.period and not derived.get("period"):
                # The feed named the half even when it sent no minute, and the
                # half is a published fact -- so it beats the derived guess.
                derived["period_label"] = self.period
                derived["period"] = self._period_key()
            return derived
        # --- source 2: a published minute from the summary feed -----------
        # Only reachable with no usable kick-off. Anchored on the last recorded
        # incident, so it is marked `feed` and the page names that incident.
        if self._latest_minute:
            since_anchor = max(0.0, (now - self.read_at).total_seconds())
            clock = {
                "minute": self._latest_minute + int(since_anchor // 60),
                "seconds": int(since_anchor % 60),
                "added": self._latest_added,
                "period": self._period_key(),
                "period_label": self.period or "",
                "period_offset": 0,
                "running": True,
                "source": "feed",
                "anchor_minute": self._latest_minute,
                "anchor_label": self.latest_event["minute_label"] if self.latest_event else None,
            }
            return self._bounded(clock)

        return derived

    def _period_key(self) -> str | None:
        label = (self.period or "").lower()
        if "1st" in label:
            return "1"
        if "2nd" in label:
            return "2"
        if "extra" in label:
            return "3"
        if "penalt" in label:
            return "4"
        return None

    def _bounded(self, clock: dict) -> dict:
        """Stop the clock counting past the end of the period it is in.

        An anchor is only as fresh as the feed. A goal at 63' followed by a
        quiet twenty minutes would otherwise run the clock to 83' while the
        second half is still really at 63'. The bound is the end of the period
        the feed last named -- 45'+ in the first half, 90'+ in the second --
        plus a generous stoppage, because running a little long is honest
        (matches do) while running away is not.
        """
        minute = clock.get("minute") or 0
        period = clock.get("period")
        if period == "1":
            ceiling = 45 + (clock.get("added") or 0) + 10
        elif period in ("2", None):
            ceiling = 90 + (clock.get("added") or 0) + 15
        else:
            ceiling = 120 + (clock.get("added") or 0) + 10
        return {**clock, "minute": min(minute, ceiling)}

    def _final_clock(self, status: str) -> dict:
        """A match not in play: the clock reads FT, HT, or nothing yet."""
        if status == "finished":
            return {"minute": None, "seconds": 0, "added": 0, "period": "ft",
                    "period_label": "Full-time", "period_offset": 0,
                    "running": False, "source": "final"}
        label = (self.period or "").lower()
        if "1st" in label and "half" in label:
            return {"minute": 45, "seconds": 0, "added": 0, "period": "ht",
                    "period_label": "Half-time", "period_offset": 0,
                    "running": False, "source": "final"}
        return {"minute": None, "seconds": 0, "added": 0, "period": None,
                "period_label": "", "period_offset": 0,
                "running": False, "source": "final"}


def derived_clock(kickoff: datetime | None, status: str, now: datetime | None = None) -> dict:
    # The fallback clock, reconstructed from the published kick-off.
    #
    # Only used when the match feed carries no minute of its own. Labelled
    # source: derived so the header can mark it, exactly as the livescore
    # board marks its own estimate.
    #
    # This delegates to flashscore.live_clock rather than modelling the halves
    # again. The board and this page are showing the same clock, and a second
    # copy of the arithmetic is how they came to disagree -- the board capping
    # at "120+'" while the header counted on. One definition, one answer.
    if status != "live":
        return {"minute": None, "seconds": 0, "added": 0, "period": None,
                "period_label": "", "period_offset": 0,
                "running": False, "source": "unavailable"}
    return fs.live_clock(kickoff, now)

def clock_label(clock: dict | None) -> str:
    """The header's clock string: ``34:24``, ``90+3:12``, ``HT``, ``FT``.

    One formatter, so the match header and every panel on the page print the
    clock identically -- three slightly different ones is how a page ends up
    saying "63" in one place and "63'" in another.

    MM:SS, counting from kick-off. Stoppage time keeps the ``+N`` the page
    already used and puts the seconds after it, so the third added minute of
    the 90th reads ``90+3:12`` rather than a bare ``93:12``: those extra
    minutes belong to the referee, not to the match clock, and folding them
    together would hide where regulation ended.
    """
    if not clock:
        return ""
    period = clock.get("period")
    if period == "ft":
        return "FT"
    if period == "ht":
        return "HT"
    minute = clock.get("minute")
    if minute is None:
        return ""
    added = clock.get("added") or 0
    seconds = clock.get("seconds") or 0
    base = f"{minute}+{added}" if added else f"{minute}"
    return f"{base}:{seconds:02d}"


def clock_label_short(clock: dict | None) -> str:
    """The board's compact form: ``63'``, ``45+2'``, ``HT``, ``FT``.

    The same model with one field fewer. The livescore board shows a hundred
    rows at once, where a ticking second hand per row is noise rather than
    information, and it refreshes far too slowly for one to be truthful.
    """
    if not clock:
        return ""
    period = clock.get("period")
    if period == "ft":
        return "FT"
    if period == "ht":
        return "HT"
    minute = clock.get("minute")
    if minute is None:
        return ""
    added = clock.get("added") or 0
    return f"{minute}+{added}'" if added else f"{minute}'"

# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

#: A section from the payload -> the heading the page prints. Unlisted sections
#: keep the feed's own name, so a new one shows up rather than vanishing.
SECTION_LABELS = {
    "Top stats": "Top stats",
    "Shots": "Shots",
    "Attack": "Attack",
    "Defence": "Defence",
    "Passing": "Passing",
    "Duels": "Duels",
    "Goalkeeping": "Goalkeeping",
}

#: Stats whose two values are shares of 100 rather than counts, so the bar is
#: the number itself instead of a ratio of the two.
PERCENT_STATS = {"Ball possession", "Passes accuracy", "Duels won"}


def _lead_number(text: str | None) -> float | None:
    """The leading number of ``"86% (337/394)"`` or ``"10"``.

    Flashscore reports some stats as a bare count, some as a percentage, and a
    few (passes) as a percentage with the underlying fraction in brackets. The
    leading number is the one the reference page shows, so that is what is
    read; the raw strings are kept too, so a caller can print the full form.
    """
    if text is None:
        return None
    token = text.strip().split(" ")[0].replace("%", "")
    try:
        return float(token)
    except ValueError:
        return None


def parse_stats(raw: str) -> list[dict]:
    """The statistics payload into display-ready sections.

    The stats payload is a *flat* key/value stream -- unlike the summary feed
    it has no record separator at all. A ``~`` prefixes a key inline to mark a
    new block (``~SF`` opens a section, ``~SD`` a group of stats), and the
    stats follow as repeating triples of ``SG`` (name), ``SH`` (home value)
    and ``SI`` (away value). Walking the stream in order is what keeps the two
    numbers with the right label -- the same reason the day feed is walked in
    order rather than read as a map.
    """
    sections: list[dict] = []
    current: dict | None = None
    pending: str | None = None

    def ensure(name: str) -> dict:
        for section in sections:
            if section["name"] == name:
                return section
        section = {"name": name, "label": SECTION_LABELS.get(name, name), "rows": []}
        sections.append(section)
        return section

    # The two sides' values sit in *adjacent* fields: `SH` carries the home
    # value and the very next field, whatever key it carries, is the away one.
    # That is read by position rather than by the name `SI`, because position
    # is what the feed actually guarantees -- and reading by name alone would
    # silently drop every stat if the second key were ever spelled differently.
    fields = raw.split(_FIELD)
    stripped: list[tuple[str, str]] = []
    for field in fields:
        key, _, value = field.partition(_KV)
        stripped.append((key.lstrip("~"), value))

    for i, (key, value) in enumerate(stripped):
        if key == "SF":
            current = ensure(value.strip() or "Match")
            pending = None
            continue
        if key == "SG":
            pending = value.strip()
            continue
        if key != "SH" or not pending:
            continue
        home_raw = value
        away_raw = stripped[i + 1][1] if i + 1 < len(stripped) else None
        home = _lead_number(home_raw)
        away = _lead_number(away_raw)
        label, pending = pending, None
        if home is None or away is None:
            continue
        total = home + away
        (current or ensure("Match"))["rows"].append({
            "label": label,
            "home": home,
            "away": away,
            "home_raw": home_raw,
            "away_raw": away_raw,
            "home_pct": round(home / total * 100, 1) if total else 50.0,
            "away_pct": round(away / total * 100, 1) if total else 50.0,
            "is_percent": label in PERCENT_STATS,
        })

    return [s for s in sections if s["rows"]]


# ---------------------------------------------------------------------------
# Match context (who is playing, in what, when)
# ---------------------------------------------------------------------------
#
# The summary feed carries the *clock and the incidents* but no team names --
# only player names inside event text. The history feed opens with the match
# itself as its own first record, and that record is the canonical context:
# `KP` the match id, `FH`/`FK` the two teams, `KF` the competition, `KH` the
# country, `KL` the score, `KC` the kick-off timestamp and `EC`/`ED` the crests.
#
# This matters because the day feed is capped -- a busy Saturday runs to
# hundreds of competitions -- so a match in a league that falls outside that cap
# is *not* in it, even though the reader clicked it on a board that showed it.
# Reading the context from the match's own feed makes the match page work for
# any match id, whether or not the day feed happened to include it.

# ---------------------------------------------------------------------------
# Lineups
# ---------------------------------------------------------------------------
#
# The lineups feed (df_li_1) is a run of player records, each carrying its own
# section (`LA`/`LB`), side (`LC`) and formation (`LD`) so the list can be
# rebuilt without any surrounding state machine: a substitute block is simply a
# run of records that name themselves as substitutes.

#: The section a record belongs to, as the feed names it. The coaches block is
#: spelled "Coaches" in the payload, plural.
_LINEUP_SECTIONS = {
    "Starting Lineups": "starting",
    "Substitutes": "substitutes",
    "Missing Players": "missing",
    "Coach": "coach",
    "Coaches": "coach",
}
#: The badge the feed puts beside a player. Spelled out because "(G)" on a pitch
#: diagram means nothing to a reader who is not looking at one.
_LINEUP_ROLES = {
    "(C)": "captain",
    "(G)": "goalkeeper",
}


#: Where a player portrait is served from. The feed publishes only the file
#: NAME (`x0F2GvEd-CKhortiC.png`, under `LPZ`) and the host is a constant, so the
#: URL is assembled here rather than being guessed per player.
_PLAYER_PHOTO_BASE = "https://static.flashscore.com/res/image/data/"

#: The feed ships the same portrait at six sizes, one per field. They are not
#: different pictures -- measured against a live payload they are 36x36, 42x42,
#: 72x72, 84x84, 108x108 and 126x126 of the same face. `LPL` (72px) is used for
#: the row because it is crisp at the 28-36px the lineup renders it at, and `LPQ`
#: (126px) is offered as the 2x source so a retina screen does not upscale a
#: 72px thumbnail into mush.
_PHOTO_ROW_KEY = "LPL"
_PHOTO_HIRES_KEY = "LPQ"
#: Falls back through these if the preferred size is missing from a record.
_PHOTO_FALLBACK_KEYS = ("LPL", "LPZ", "LPX", "LPY", "LPI", "LPQ")


def _player_photo(fields: dict) -> tuple[str | None, str | None]:
    """Return (row_url, hires_url) for a player record, or (None, None).

    The fields are independent -- a record can carry some sizes and not others --
    so the row falls back through the list rather than trusting one key. Absence
    is returned as None and the UI renders initials instead: not every player in
    the feed has a portrait, and a broken-image icon would be a worse answer than
    a monogram.
    """
    def url(key: str) -> str | None:
        name = (fields.get(key) or "").strip()
        if not name:
            return None
        # The feed value is a bare filename. Guard against it already being a
        # full URL (or a protocol-relative one) so this cannot produce
        # "https://.../https://...".
        if name.startswith("http://") or name.startswith("https://"):
            return name
        if name.startswith("//"):
            return f"https:{name}"
        return _PLAYER_PHOTO_BASE + name.lstrip("/")

    row = next((url(k) for k in _PHOTO_FALLBACK_KEYS if url(k)), None)
    return row, url(_PHOTO_HIRES_KEY)


def _lineup_player(record: str, section: str, side: str | None, formation: str | None) -> dict | None:
    fields = _fields(record)
    name = (fields.get("LI") or fields.get("LN") or "").strip()
    if not name:
        return None
    badge = (fields.get("LR") or "").strip()
    photo, photo_hires = _player_photo(fields)
    return {
        "name": name,
        "short_name": (fields.get("LN") or "").strip() or None,
        "player_id": (fields.get("LP") or "").strip() or None,
        "shirt": _as_int(fields.get("LJ")),
        "position": _as_int(fields.get("LH")),
        "role": _LINEUP_ROLES.get(badge, badge or None),
        "role_label": (fields.get("LS") or "").strip() or None,
        "rating": (fields.get("LPR") or "").strip() or None,
        "photo": photo,
        "photo_hires": photo_hires,
        "section": section,
        "team": side,
        "formation": formation,
    }


def parse_lineups(raw: str) -> dict:
    """The lineups feed into ``{home: {...}, away: {...}}``.

    Both sides' starting XI with their formation, and the substitutes each. The
    formation string ("1-4-2-3-1") is carried as published rather than turned
    into coordinates: it is what a reader wants beside the list, and a pitch
    diagram built from a guessed layout is a worse lie than no diagram.
    """
    teams: dict[str, dict] = {
        "home": {"formation": None, "starting": [], "substitutes": [], "coach": None},
        "away": {"formation": None, "starting": [], "substitutes": [], "coach": None},
    }
    if not raw:
        return {"available": False, "home": teams["home"], "away": teams["away"]}

    side: str | None = None
    formation: str | None = None
    section = "starting"

    for record in raw.split(_RECORD):
        if not record.strip():
            continue
        header = _fields(record)
        # A block header names the section and the side, and every following
        # player record inherits them -- so they are tracked here rather than
        # read per player. `LC` (the side) sits on the header alone; the player
        # records carry no side of their own, which is why reading it per record
        # put both teams in one list.
        block = (header.get("LB") or header.get("LA") or "").strip()
        name_only = not header.get("LP") and not header.get("LI")
        # `LC` names the side and appears in three shapes: on a section header
        # ("Starting Lineups", side 1), on a record with nothing else at all
        # (`LC=2` alone, which is how the away list opens), and on the coach
        # block. Reading it only from the first shape left every player on the
        # home side, because the away list is opened by the bare marker.
        if name_only and header.get("LC"):
            side = "home" if _as_int(header["LC"]) == 1 else "away"
        if block in _LINEUP_SECTIONS and name_only:
            section = _LINEUP_SECTIONS[block]
            continue
        if name_only:
            continue
        # The formation rides on the FIRST PLAYER of a block rather than on the
        # header, so it is read here and remembered for the players that follow.
        if header.get("LD", "").strip():
            formation = header["LD"].strip()
            if side:
                teams[side]["formation"] = formation
        player = _lineup_player(record, section, side, formation)
        if player is None or side is None:
            continue
        if section == "starting":
            teams[side]["starting"].append(player)
        elif section == "substitutes":
            teams[side]["substitutes"].append(player)
        elif section == "coach":
            teams[side]["coach"] = player["name"]

    # The pitch layout is derived from the published formation, never invented:
    # a side whose formation is missing or unreadable simply gets no `pitch`,
    # and the UI falls back to the list rather than drawing a shape it cannot
    # justify.
    for team in teams.values():
        team["pitch"] = build_pitch(team.get("formation"), team.get("starting") or [])

    available = bool(teams["home"]["starting"] or teams["away"]["starting"])
    return {"available": available, "home": teams["home"], "away": teams["away"]}


def build_pitch(formation: str | None, starting: list[dict]) -> dict | None:
    """Group a starting XI into pitch lines, back to front.

    Returns ``{"lines": [[player, ...], ...], "keeper": player}`` with the
    keeper in their own leading line, or ``None`` when the formation is missing,
    unreadable, or describes a different number of players than the feed sent.

    The last clause is the important one. Grouping 11 players by a formation
    that claims 10 (or 12) silently places the wrong shirts on the field, so a
    mismatch is refused outright: no pitch is a better answer than a wrong one,
    and the UI treats None as "show the list".
    """
    lines = formation_lines(formation)
    derived = False
    if not lines:
        # The feed publishes no formation for a minority of matches (measured:
        # 4 of 60) even though it sends the full eleven. Refusing outright meant
        # those matches showed a bare list where every other match showed a
        # pitch -- the one case a reader notices as "the lineup is missing".
        #
        # The feed's own record order is the only remaining signal, and it is a
        # real one: it lists the keeper first and then the outfield players from
        # the back forward (verified against the raw payload, which carries no
        # x/y of any kind -- `LH` is a display index 0..10 and `LO` is not a
        # coordinate). So a thinned shape is derived from that order and marked
        # `derived` so the UI can say where it came from rather than passing it
        # off as published.
        lines = _derive_lines(starting)
        if lines is None:
            return None
        derived = True
    # The keeper is not in the formation string's outfield digits; it is the
    # leading 1 and is always its own line.
    outfield = list(starting)
    keeper = next((p for p in outfield if (p.get("role") or "") == "goalkeeper"), None)
    if keeper is None:
        # Fall back to the feed's own ordering: the keeper is recorded first.
        keeper = outfield[0] if outfield else None
    if keeper is not None:
        outfield = [p for p in outfield if p is not keeper]

    if len(outfield) != sum(lines):
        return None
    grouped: list[list[dict]] = []
    index = 0
    for size in lines:
        grouped.append(outfield[index : index + size])
        index += size
    return {
        "keeper": keeper,
        "lines": grouped,
        # The shape as published, so the UI can label the pitch without
        # re-deriving it from the grouped counts. For a derived shape this is the
        # shape that was inferred, and `derived` records that fact.
        "formation": formation if not derived else _format_formation(lines),
        #: True when the feed published no formation and this shape was inferred
        #: from the player order. The UI uses it to label the figure honestly.
        "derived": derived,
    }


def _derive_lines(starting: list[dict]) -> list[int] | None:
    """Infer outfield line sizes from the feed's own player order.

    Only used when the feed publishes no formation. The order is keeper-first and
    then back-to-front, so a defensible shape is the standard 4-4-2 spine -- but
    it is a guess, and the caller marks it as such.

    Returns None unless there are exactly eleven players, because a shape drawn
    for the wrong number of shirts is worse than no shape at all.
    """
    if len(starting) != 11:
        return None
    # 4-4-2 is the most common formation in the feed (measured across 33 matches
    # with a published shape it was the single most frequent). The striker order
    # within the lines still comes from the feed, so this only fixes the SHAPE,
    # never the identity or the sequence of the players.
    return [4, 4, 2]


def _format_formation(lines: list[int]) -> str:
    """Render derived outfield lines as a formation string ("1-4-4-2")."""
    return "-".join(str(n) for n in [1, *lines])

#: The keeper's line index in a formation string. "1-4-2-3-1" is the keeper plus
#: four lines of outfielders, so the digits after the first are the outfield
#: lines read from the back.

def formation_lines(formation: str | None) -> list[int] | None:
    """The outfield line sizes a formation string names, or None if unreadable.

    "1-4-2-3-1" -> [4, 2, 3, 1]. The leading 1 (the keeper) is dropped: the
    keeper is always a line of its own at the back, and the caller places them.

    This is the ONLY spatial information the feed publishes. It was checked
    directly before relying on it: `LO` looks like it could be a coordinate but
    is not -- it takes only 3 distinct values across an 11-player side and 7
    across the other, where a left-to-right axis needs one value per player.
    `LL` is a display order (1..11), not a position. So the *shape* below is
    derived from the published formation, and the left-to-right order within a
    line is the feed's own record order -- which is never presented as a
    measured x/y.
    """
    if not formation:
        return None
    parts = [p for p in formation.replace("-", " ").split() if p.strip()]
    if len(parts) < 2:
        return None
    try:
        numbers = [int(p) for p in parts]
    except ValueError:
        return None
    # A formation must describe exactly eleven players, or the caller drawing a
    # pitch from it would place a wrong number of shirts on the field.
    if sum(numbers) != 11:
        return None
    return numbers[1:]


def fetch_lineups(match_id: str) -> dict:
    # The lineups for a match, or an unavailable block when the feed has none.
    return parse_lineups(_fetch(f"/x/feed/df_li_1_{match_id}"))

def parse_match_context(raw: str, match_id: str) -> dict | None:
    # The match's own context record, or None when the feed lacks it.
    #
    # The first record whose `KP` equals the match id is the current match;
    # every following record is one of the form/h2h history rows, so a match is
    # taken from the first hit and the walk stops there.
    for record in raw.split(_RECORD):
        fields = _fields(record)
        if fields.get("KP") != match_id:
            continue
        home = (fields.get("FH") or "").strip()
        away = (fields.get("FK") or "").strip()
        if not home or not away:
            continue
        kickoff_ts = _as_int(fields.get("KC"))
        score = (fields.get("KL") or "").strip()
        home_goals = away_goals = None
        if ":" in score:
            left, _, right = score.partition(":")
            home_goals, away_goals = _as_int(left), _as_int(right)
        country = (fields.get("KH") or "").strip()
        competition = (fields.get("KF") or "").strip()
        return {
            "match_id": match_id,
            "home_team": {"name": home, "logo": fs._crest(fields.get("EC"))},
            "away_team": {"name": away, "logo": fs._crest(fields.get("ED"))},
            "competition": {
                "name": competition or None,
                "country": country or None,
                "label": (
                    f"{country} - {competition}" if country and competition else competition or None
                ),
            },
            "kickoff": (
                datetime.fromtimestamp(kickoff_ts, tz=timezone.utc)
                if kickoff_ts is not None
                else None
            ),
            "home_goals": home_goals,
            "away_goals": away_goals,
        }
    return None

def fetch_match_context(match_id: str) -> dict | None:
    # The context record for a match id, or None when the feed is unreachable.
    raw = _fetch(f"/x/feed/df_hh_1_{match_id}")
    if not raw:
        return None
    return parse_match_context(raw, match_id)


def parse_context_status(raw: str, match_id: str) -> int | None:
    # The status code the history feed attaches to a match's context record.
    #
    # Careful: `AC` means two different things in the two feeds. In the summary
    # feed it is the *period name* ("1st Half"); in the history feed it is a
    # *status code*, where 3 marks a finished match. Reading one as the other is
    # exactly how a match in play ends up displayed as over, so the two parsers
    # never share a helper.
    for record in raw.split(_RECORD):
        fields = _fields(record)
        if fields.get("KP") != match_id:
            continue
        return _as_int(fields.get("AC"))
    return None


def status_from_signals(
    *,
    kickoff: datetime | None,
    code: int | None,
    has_period: bool,
    now: datetime | None = None,
) -> str:
    # "finished" | "live" | "scheduled", from the signals the feeds publish.
    #
    # This is deliberately explicit about *why* it decides what it decides,
    # because the honest answer is that neither feed carries a reliable live
    # flag on its own:
    #
    #   * the history feed's status code (3 = over) is authoritative when set;
    #   * the summary feed published a period, so the match has kicked off;
    #   * kick-off time bounds what is *possible* -- a match cannot still be in
    #     the 63rd minute six hours after it started.
    #
    # When those disagree the *more conservative* reading wins: a match shown as
    # live but actually over is a much smaller error than a match shown as over
    # while it is still running, so "started and not provably over" reads live.
    now = now or datetime.now(timezone.utc)
    if code == 3:
        return "finished"
    if not has_period:
        return "scheduled"
    if kickoff is None:
        return "live"
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    elapsed = (now - kickoff).total_seconds() / 60
    # A match that kicked off within the last three hours can still be in play;
    # beyond that it has certainly finished and the feed simply has not said so.
    if elapsed < 0:
        return "scheduled"
    return "live" if elapsed <= 180 else "finished"


def fetch_status(match_id: str, kickoff: datetime | None = None) -> str:
    # "finished" | "live" | "scheduled" for a match id, from its own feeds.
    summary = _fetch(f"/x/feed/df_sui_1_{match_id}")
    has_period = bool(summary and MatchFeed(match_id, summary).period)
    code = parse_context_status(_fetch(f"/x/feed/df_hh_1_{match_id}"), match_id)
    return status_from_signals(kickoff=kickoff, code=code, has_period=has_period)

# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def _fetch(path: str) -> str:
    """A feed read that degrades to ``""`` instead of raising.

    Every caller here is building a *page*: an upstream that is down must leave
    the page showing what the database already knows, never a stack trace.
    """
    try:
        return fs._fetch(path)
    except fs.FlashscoreUnavailable:
        return ""


def build_match_detail(
    match_id: str,
    *,
    kickoff: datetime | None = None,
    status: str = "scheduled",
    home_goals: int | None = None,
    away_goals: int | None = None,
    now: datetime | None = None,
) -> dict:
    """Everything the match page needs for one match, from the feeds.

    Never raises: an unreachable feed yields ``available: False`` with the
    clock marked ``derived`` or ``unavailable``, and the caller renders what the
    local database already holds. A match page that refuses to open because an
    upstream is down is worse than one that opens and says so.
    """
    now = now or datetime.now(timezone.utc)
    raw = _fetch(f"/x/feed/df_sui_1_{match_id}")
    feed = MatchFeed(match_id, raw, read_at=now)

    clock = feed.clock(kickoff, status, now)

    # The summary feed answers *something* for an id it does not know -- an
    # empty envelope with no period and no incidents. "The feed replied" is
    # therefore not the same claim as "the feed has this match", and only the
    # second one justifies calling the live data available. Otherwise a typo in
    # the URL renders as a live match with a blank clock.
    feed_knows_match = bool(feed.period or feed.events)

    # The feed's score is fresher than the database's between syncs, so prefer
    # it once the match is under way; before that the stored score is the only
    # one that exists.
    feed_has_score = bool(feed.events) or status in ("live", "finished")
    stats_raw = _fetch(f"/x/feed/df_st_1_{match_id}") if feed_knows_match else ""
    stats = parse_stats(stats_raw) if stats_raw else []
    lineups = fetch_lineups(match_id) if feed_knows_match else parse_lineups("")

    return {
        "available": feed_knows_match,
        "match_id": match_id,
        "fetched_at": now.isoformat(),
        "clock": clock,
        "clock_label": clock_label(clock),
        "period": feed.period,
        "score": {
            "home": feed.score[0] if feed_has_score else home_goals,
            "away": feed.score[1] if feed_has_score else away_goals,
            "source": "feed" if feed_has_score else "database",
        },
        "events": feed.events,
        "stats": stats,
        "stats_available": bool(stats),
        "lineups": lineups,
        "info": feed.info,
    }
