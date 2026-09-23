"""Real team results history, so the model can rate real clubs.

Why this exists
---------------
The prediction engine rates a team from its recent results: an `attack` and
`defence` multiplier per side, recency-weighted and shrunk toward the league
mean. Without any results to read, every real club sits at the neutral 1.0 and
the engine cannot tell Real Madrid from Elche -- which is exactly the state the
site was in once real fixtures arrived.

Flashscore exposes a per-match feed, ``df_hh_1_<matchId>``, that carries both
teams' recent form plus their head-to-head. This module parses it into plain
records the sync can turn into finished ``Fixture`` rows.

Verified field map (probed against live payloads, not guessed)
-------------------------------------------------------------
``KB``  block title, e.g. ``"Last matches: Uganda U20"`` / ``"Head-to-head matches"``
``KC``  kick-off, Unix seconds
``KF``  competition name
``FH``  home team          ``FJ``  home team slug
``FK``  away team          ``FL``  away team slug
``KU``  home goals         ``KT``  away goals
``KL``  display scoreline, e.g. ``"3:1"``
``KS``  venue for the listed team: ``home`` | ``away``
``WIS`` that team's result: ``w`` | ``d`` | ``l`` (``lo``/``wo`` = after ET)

Only ``KU``/``KT`` are real goals. A row missing them is skipped rather than
recorded as 0-0.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from . import flashscore as fs

#: Field separator inside a record, and record separator between records.
#: The feed uses single-byte Latin-1 characters (¬ and ~).
_FIELD = "\xac"
_KV = "\xf7"
_RECORD = "\x7e"


@dataclass
class HistoryMatch:
    """One finished match from a team's recent form."""

    timestamp: int
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    competition: str
    venue: str | None = None
    result: str | None = None
    # Half-time score (KX/KY). None when the feed did not carry it.
    half_home: int | None = None
    half_away: int | None = None
    # W/D/L as a single letter (BZ), for a compact form strip.
    outcome: str | None = None
    home_logo: str | None = None
    away_logo: str | None = None
    @property
    def kickoff(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc)

    @property
    def total_goals(self) -> int:
        return self.home_goals + self.away_goals
    @property
    def btts(self) -> bool:
        return self.home_goals > 0 and self.away_goals > 0
    @property
    def over25(self) -> bool:
        return self.total_goals > 2.5


def _fields(record: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in record.split(_FIELD):
        if _KV in pair:
            key, _, value = pair.partition(_KV)
            out[key] = value
    return out


def _as_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _crest(name: str | None) -> str | None:
    """The feed ships crest *filenames*; build the public image URL."""
    if not name:
        return None
    if name.startswith("http"):
        return name
    return f"https://static.flashscore.com/res/image/data/{name}"


def parse_history(raw: str) -> tuple[list[HistoryMatch], list[HistoryMatch]]:
    """Split a ``df_hh`` payload into (team form, head-to-head).

    Both lists are de-duplicated by (timestamp, teams) because the payload
    repeats each match across several blocks.
    """
    blocks = parse_sections(raw)
    form: list[HistoryMatch] = []
    h2h: list[HistoryMatch] = []
    seen_form: set[tuple] = set()
    seen_h2h: set[tuple] = set()

    for block in blocks:
        is_h2h = block["kind"] == "h2h"
        target = h2h if is_h2h else form
        seen = seen_h2h if is_h2h else seen_form
        for match in block["matches"]:
            key = (match.timestamp, match.home_team, match.away_team)
            if key in seen:
                continue
            seen.add(key)
            target.append(match)

    return form, h2h


def parse_sections(raw: str) -> list[dict]:
    """Parse a ``df_hh`` payload into its named blocks.

    The feed is not a flat list: matches are grouped under headings such as
    ``Overall``, ``Last matches: Arsenal`` and ``Head-to-head matches``. The
    match page needs that grouping -- to show one team's form separately from
    the other's -- so the section each row belongs to is tracked, not discarded.

    Returns ``{title, kind, team, matches}`` per block, where ``kind`` is one of
    ``form`` | ``h2h`` | ``other``.
    """
    blocks: list[dict] = []
    current: dict | None = None

    for record in raw.split(_RECORD):
        record = record.strip()
        if not record:
            continue
        f = _fields(record)

        if "KB" in f:
            title = (f["KB"] or "").strip()
            current = {
                "title": title,
                "kind": _block_kind(title),
                "team": _block_team(title),
                "matches": [],
            }
            blocks.append(current)
            continue

        home = (f.get("FH") or "").strip()
        away = (f.get("FK") or "").strip()
        ts = _as_int(f.get("KC"))
        hg = _as_int(f.get("KU"))
        ag = _as_int(f.get("KT"))

        # Only fully-played matches with a real scoreline are usable: a row with
        # no goals is skipped, never recorded as 0-0.
        if not home or not away or ts is None or hg is None or ag is None:
            continue

        match = HistoryMatch(
            timestamp=ts,
            home_team=home,
            away_team=away,
            home_goals=hg,
            away_goals=ag,
            competition=(f.get("KF") or "").strip(),
            venue=f.get("KS") or None,
            result=f.get("WIS") or None,
            half_home=_as_int(f.get("KX")),
            half_away=_as_int(f.get("KY")),
            outcome=(f.get("BZ") or "").strip().lower() or None,
            home_logo=_crest(f.get("EC")),
            away_logo=_crest(f.get("ED")),
        )
        if current is None:
            current = {"title": "Matches", "kind": "other", "team": None, "matches": []}
            blocks.append(current)
        current["matches"].append(match)

    return blocks


def _block_kind(title: str) -> str:
    low = title.lower()
    if low.startswith("head-to-head"):
        return "h2h"
    if low.startswith("last matches") or low == "overall":
        return "form"
    return "other"


def _block_team(title: str) -> str | None:
    """The team a ``"Last matches: X"`` block belongs to, if it names one."""
    if ":" in title:
        _before, _sep, team = title.partition(":")
        return team.strip() or None
    return None


def fetch_history(match_id: str) -> tuple[list[HistoryMatch], list[HistoryMatch]]:
    """Recent form + head-to-head for the two teams in ``match_id``.

    Never raises for a feed problem: an unreachable upstream yields empty lists,
    so a caller simply gets no extra history rather than a failed sync.
    """
    try:
        raw = fs._fetch(f"/x/feed/df_hh_1_{match_id}")
    except fs.FlashscoreUnavailable:
        return [], []
    return parse_history(raw)
def form_summary(matches: list, team_name: str | None = None) -> dict:
    """Aggregate a list of matches into the stats a match page shows.

    Every number is derived from the matches themselves, so nothing here is
    invented: goals for/against, W-D-L, and the derived rates (BTTS, over 2.5,
    clean sheets) are counted, not estimated.

    ``team_name`` scopes the tally to one side -- needed because a form block
    lists matches for a specific club, whereas a head-to-head block contains
    both.
    """
    played = len(matches)
    empty = {
        "played": 0,
        "won": 0,
        "drawn": 0,
        "lost": 0,
        "goals_for": 0,
        "goals_against": 0,
        "goal_diff": 0,
        "avg_goals_for": 0.0,
        "avg_goals_against": 0.0,
        "btts_rate": 0.0,
        "over25_rate": 0.0,
        "clean_sheets": 0,
        "failed_to_score": 0,
        "form": [],
        "points_per_game": 0.0,
    }
    if not played:
        return empty

    won = drawn = lost = gf = ga = cs = fts = 0
    btts = over25 = 0
    form_letters: list[str] = []

    for m in matches:
        if team_name:
            # Which side is "us" in this row?
            if m.home_team.lower() == team_name.lower():
                scored, conceded = m.home_goals, m.away_goals
            elif m.away_team.lower() == team_name.lower():
                scored, conceded = m.away_goals, m.home_goals
            else:
                # A neutral-venue row for neither side: skip rather than guess.
                continue
        else:
            scored, conceded = m.home_goals, m.away_goals

        gf += scored
        ga += conceded
        if scored > conceded:
            won += 1
            form_letters.append("W")
        elif scored == conceded:
            drawn += 1
            form_letters.append("D")
        else:
            lost += 1
            form_letters.append("L")
        if conceded == 0:
            cs += 1
        if scored == 0:
            fts += 1
        if m.btts:
            btts += 1
        if m.over25:
            over25 += 1

    counted = won + drawn + lost
    if counted == 0:
        return empty

    return {
        "played": counted,
        "won": won,
        "drawn": drawn,
        "lost": lost,
        "goals_for": gf,
        "goals_against": ga,
        "goal_diff": gf - ga,
        "avg_goals_for": round(gf / counted, 2),
        "avg_goals_against": round(ga / counted, 2),
        "btts_rate": round(btts / counted, 3),
        "over25_rate": round(over25 / counted, 3),
        "clean_sheets": cs,
        "failed_to_score": fts,
        # Most recent first, matching how the feed lists them.
        "form": form_letters,
        "points_per_game": round((won * 3 + drawn) / counted, 2),
    }


def match_to_dict(m) -> dict:
    """Serialise one history match for the API."""
    return {
        "kickoff": m.kickoff.isoformat(),
        "home_team": m.home_team,
        "away_team": m.away_team,
        "home_goals": m.home_goals,
        "away_goals": m.away_goals,
        "half_home": m.half_home,
        "half_away": m.half_away,
        "competition": m.competition,
        "venue": m.venue,
        "result": m.result,
        "outcome": m.outcome,
        "home_logo": m.home_logo,
        "away_logo": m.away_logo,
    }


def fetch_match_context(match_id: str, home_name: str, away_name: str) -> dict:
    """Everything the match page needs: both sides' form, H2H and stats.

    Shaped for direct display: each block carries its own matches *and* the
    aggregate stats for those matches, so a caller never has to recompute them.
    """
    blocks = []
    try:
        raw = fs._fetch(f"/x/feed/df_hh_1_{match_id}")
        blocks = parse_sections(raw)
    except fs.FlashscoreUnavailable:
        return {
            "available": False,
            "reason": "feed_unavailable",
            "home_form": None,
            "away_form": None,
            "head_to_head": None,
        }

    home_matches: list = []
    away_matches: list = []
    h2h_matches: list = []

    for block in blocks:
        team = block["team"]
        if block["kind"] == "h2h":
            h2h_matches.extend(block["matches"])
        elif block["kind"] == "form":
            # Route the block to whichever side it names; an unnamed "Overall"
            # block is ignored in favour of the named per-team blocks.
            if team and team.lower() == home_name.lower():
                home_matches.extend(block["matches"])
            elif team and team.lower() == away_name.lower():
                away_matches.extend(block["matches"])

    # Fall back to the flat form list when the feed did not name the blocks:
    # a page with no form at all is worse than one built from unscoped matches.
    if not home_matches or not away_matches:
        flat_form, _ = parse_history(raw)
        for m in flat_form:
            if not home_matches and home_name.lower() in (m.home_team.lower(), m.away_team.lower()):
                home_matches.append(m)
            if not away_matches and away_name.lower() in (m.home_team.lower(), m.away_team.lower()):
                away_matches.append(m)

    # De-duplicate while preserving order.
    def _dedupe(items: list) -> list:
        seen = set()
        out = []
        for m in items:
            key = (m.timestamp, m.home_team, m.away_team)
            if key in seen:
                continue
            seen.add(key)
            out.append(m)
        return out

    home_matches = _dedupe(home_matches)[:10]
    away_matches = _dedupe(away_matches)[:10]
    h2h_matches = _dedupe(h2h_matches)[:10]

    return {
        "available": bool(home_matches or away_matches or h2h_matches),
        "home_form": {
            "team": home_name,
            "stats": form_summary(home_matches, home_name),
            "matches": [match_to_dict(m) for m in home_matches],
        },
        "away_form": {
            "team": away_name,
            "stats": form_summary(away_matches, away_name),
            "matches": [match_to_dict(m) for m in away_matches],
        },
        "head_to_head": {
            "stats": head_to_head_summary(h2h_matches, home_name, away_name),
            "matches": [match_to_dict(m) for m in h2h_matches],
        },
    }


def head_to_head_summary(matches: list, home_name: str, away_name: str) -> dict:
    """Wins/draws/goals for a head-to-head list, from the first team's view."""
    home_wins = away_wins = draws = hg = ag = 0
    btts = over25 = 0
    for m in matches:
        # Orient every row so the first team's goals are counted consistently,
        # regardless of which side was at home in that meeting.
        if m.home_team.lower() == home_name.lower():
            a, b = m.home_goals, m.away_goals
        elif m.away_team.lower() == home_name.lower():
            a, b = m.away_goals, m.home_goals
        else:
            continue
        hg += a
        ag += b
        if a > b:
            home_wins += 1
        elif a < b:
            away_wins += 1
        else:
            draws += 1
        if m.btts:
            btts += 1
        if m.over25:
            over25 += 1

    played = home_wins + away_wins + draws
    return {
        "played": played,
        "home_wins": home_wins,
        "away_wins": away_wins,
        "draws": draws,
        "home_goals": hg,
        "away_goals": ag,
        "btts_rate": round(btts / played, 3) if played else 0.0,
        "over25_rate": round(over25 / played, 3) if played else 0.0,
    }
