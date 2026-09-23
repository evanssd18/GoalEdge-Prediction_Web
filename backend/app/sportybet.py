"""SportyBet market board — a read-only odds display.

This module is deliberately **separate from the prediction engine**. The engine
prices three markets (1X2, Over/Under, BTTS) off its Dixon-Coles matrix, and
nothing here ever feeds back into it. It exists so the fixture page can show
what the book is actually offering, clearly labelled as the book's numbers
rather than a GoalEdge claim.

Two responsibilities:

1. ``fetch_event_markets(event_id)`` — load a real SportyBet event and
   normalise its market list into a stable shape.
2. ``fetch_fixture_board(fixture)`` — find the event matching a fixture by
   team name and kickoff, then return normalised markets.

Team-name matching is exact-after-normalisation only. A wrong match here would
put another match's prices on the card, which is worse than showing nothing,
so no fuzzy scoring is applied.
"""
from __future__ import annotations

import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import markets
from .config import settings
from .models import Fixture

# --------------------------------------------------------------------------
# normalisation helpers
# --------------------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# Club-name noise that differs between feeds but never changes identity. These
# are *prefixes* ("FC Koln", "SC Freiburg", "AC Milan") or *suffixes*
# ("Como Calcio") depending on the club, so both ends are stripped.
_NAME_PREFIXES = ("fc", "cf", "sc", "ac", "afc", "as", "ss", "us", "sv", "vfl", "vfb", "tsg")
_NAME_SUFFIXES = ("calcio", "football club", "fc", "cf", "sc", "ac")


def normalise_team_name(name: str | None) -> str:  # noqa: D401
    """Lower-case, strip accents, punctuation and club-name noise.

    ``"Como 1907"`` and ``"Como"`` both reduce to ``"como"``; ``"Bayern
    München"`` and ``"Bayern Munchen"`` reduce to ``"bayern munchen"``.
    """
    if not name:
        return ""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = _NON_ALNUM.sub(" ", ascii_name.lower()).strip()
    # Drop a trailing founding year ("como 1907" -> "como").
    s = re.sub(r"\s+(?:19|20)\d{2}$", "", s).strip()
    # Strip club-type affixes from both ends, but never the whole name, so
    # "AC Milan" -> "milan" while a bare "Arouca" stays intact.
    for affix in _NAME_PREFIXES:
        stripped = re.sub(r"^" + affix + r"\s+", "", s).strip()
        if stripped:
            s = stripped
    for affix in _NAME_SUFFIXES:
        stripped = re.sub(r"\s+" + affix + r"$", "", s).strip()
        if stripped:
            s = stripped
    return _NON_ALNUM.sub(" ", s).strip()


def _as_float(value) -> float | None:
    """Best-effort decimal-odds parse. Returns None for non-prices."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return f if f > 1.0 else None


def _market_group(name: str) -> str:
    """Display label for the category a market name belongs to.

    Delegates to the shared taxonomy in :mod:`app.markets`, which also decides
    whether the engine can price the category. One classifier means the label
    shown in the UI and the `model_priced` flag can never disagree.
    """
    return markets.BY_KEY[markets.categorise(name)].label


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class SportyBetUnavailable(RuntimeError):
    """Raised when the board cannot be loaded; callers degrade gracefully."""


def _get_json(path: str, params: dict) -> dict:
    query = urllib.parse.urlencode({**params, "_t": int(datetime.now().timestamp())})
    url = f"{settings.sportybet_base_url.rstrip('/')}/{path.lstrip('/')}?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; GoalEdgeAI/1.0)",
            "Accept": "application/json",
            "Referer": "https://www.sportybet.com/",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.sportybet_timeout_seconds) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SportyBetUnavailable(f"SportyBet request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SportyBetUnavailable("SportyBet returned a non-JSON response") from exc

    if not isinstance(payload, dict):
        raise SportyBetUnavailable("Unexpected SportyBet payload")
    # SportyBet wraps everything in {bizCode, message, data}
    if payload.get("bCode") not in (None, 10000):
        raise SportyBetUnavailable(payload.get("message") or "SportyBet rejected the request")
    return payload


# --------------------------------------------------------------------------
# market normalisation
# --------------------------------------------------------------------------

def _normalise_market(raw: dict) -> dict | None:
    """Turn one raw SportyBet market into {name, group, selections[]}."""
    name = str(raw.get("desc") or raw.get("name") or raw.get("marketName") or "").strip()
    outcomes = raw.get("outcomes") or raw.get("selections") or raw.get("odds") or []
    if not name or not isinstance(outcomes, list):
        return None

    selections: list[dict] = []
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        label = str(
            outcome.get("desc")
            or outcome.get("name")
            or outcome.get("outcomeName")
            or ""
        ).strip()
        odds = _as_float(
            outcome.get("odds")
            or outcome.get("price")
            or outcome.get("odd")
            or outcome.get("value")
        )
        if not label or odds is None:
            continue
        selections.append(
            {
                "label": label,
                "odds": odds,
                "specifier": outcome.get("specifier") or raw.get("specifier"),
            }
        )

    if not selections:
        return None
    key = markets.categorise(name)
    category = markets.BY_KEY[key]
    return {
        "name": name,
        "group": category.label,
        "group_key": key,
        # Surface whether the engine can price this. The frontend renders a
        # "model" badge from this flag, so the claim is data-driven rather than
        # hardcoded per category.
        "model_priced": category.model_priced,
        "market_id": raw.get("id") or raw.get("marketId"),
        "selections": selections,
    }


def normalise_event_markets(payload: dict) -> list[dict]:
    """Pull the market list out of a SportyBet event payload."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        data = data.get("event") or data.get("data") or data
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return []

    # Named `found`, not `markets`: the latter would shadow the imported
    # taxonomy module for the rest of this function.
    found: list[dict] = []
    seen: set[str] = set()
    for raw in data.get("markets") or []:
        if not isinstance(raw, dict):
            continue
        market = _normalise_market(raw)
        if market is None:
            continue
        # The feed repeats markets per specifier; key on name+id so a handicap
        # line is not collapsed into the plain market.
        dedupe_key = f"{market['name']}|{market.get('market_id')}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        found.append(market)
    return found


def group_markets(board: list[dict], groups: list[str] | None = None) -> list[dict]:
    """Group a flat market list for display, in canonical category order.

    Ordering comes from the taxonomy rather than the feed, so the panel reads
    the same way across fixtures and books.
    """
    wanted = [g.strip().lower() for g in groups] if groups else None
    buckets: dict[str, list[dict]] = {}
    for market in board:
        if wanted and market["group"].lower() not in wanted:
            continue
        buckets.setdefault(market["group"], []).append(market)

    ordered = sorted(buckets.items(), key=lambda kv: markets.order_index(_key_for(kv[0])))
    return [
        {
            "group": name,
            "group_key": _key_for(name),
            # Every market in a group shares the flag, but exposing it per group
            # lets the UI badge the heading without inspecting selections.
            "model_priced": any(m.get("model_priced") for m in items),
            "markets": items,
        }
        for name, items in ordered
    ]


def _key_for(label: str) -> str:
    """Reverse-lookup a category key from its display label."""
    for cat in markets.CATEGORIES:
        if cat.label == label:
            return cat.key
    return "other"


# --------------------------------------------------------------------------
# event lookup
# --------------------------------------------------------------------------

def _iter_events(payload: dict) -> list[dict]:
    """Walk a SportyBet sport-list payload and yield every event dict."""
    events: list[dict] = []
    data = payload.get("data") if isinstance(payload, dict) else None
    tournaments = (data or {}).get("tournaments") if isinstance(data, dict) else None
    if not isinstance(tournaments, list):
        return events
    for tournament in tournaments:
        if not isinstance(tournament, dict):
            continue
        for event in tournament.get("events") or []:
            if isinstance(event, dict):
                events.append(event)
    return events


def fetch_events_for_date(when: datetime) -> list[dict]:
    """All football events SportyBet lists for a calendar day."""
    payload = _get_json(
        f"/{settings.sportybet_country}/sport/football/events",
        {
            "productId": settings.sportybet_product_id,
            "date": when.strftime("%Y-%m-%d"),
        },
    )
    return _iter_events(payload)


def _event_kickoff(event: dict) -> datetime | None:
    for key in ("estimateStartTime", "startTime", "kickOffTime", "matchTime"):
        raw = event.get(key)
        if raw is None:
            continue
        if isinstance(raw, (int, float)) or str(raw).isdigit():
            ts = float(raw)
            # Milliseconds vs seconds vs the feed's "yyyyMMddHHmmss" style.
            if ts > 1e11:
                ts /= 1000
            elif ts > 1e9:
                pass
            else:
                continue
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def find_event_for_fixture(fixture: Fixture, events: list[dict]) -> dict | None:
    """Locate the SportyBet event matching this fixture.

    Match is exact on normalised team names, with the kickoff required within
    a tolerance window so a repeat fixture in another season can't match.
    """
    home = normalise_team_name(fixture.home_team.name)
    away = normalise_team_name(fixture.away_team.name)
    if not home or not away:
        return None

    kickoff = fixture.kickoff
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    tolerance = timedelta(hours=6)

    for event in events:
        e_home = normalise_team_name(event.get("homeTeam") or event.get("home"))
        e_away = normalise_team_name(event.get("awayTeam") or event.get("away"))
        if e_home != home or e_away != away:
            continue
        event_time = _event_kickoff(event)
        if event_time is not None and abs(event_time - kickoff) > tolerance:
            continue
        return event
    return None


def fetch_event_markets(event_id: str) -> list[dict]:
    """Every market SportyBet offers on one event, normalised for display."""
    payload = _get_json(
        f"/{settings.sportybet_country}/sport/event",
        {"productId": settings.sportybet_product_id, "eventId": event_id},
    )
    return normalise_event_markets(payload)


# --------------------------------------------------------------------------
# top-level entry point
# --------------------------------------------------------------------------

#: Groups the fixture page asks for by default. Everything else the book
#: offers is still returned, just not pinned to the top of the board.
DEFAULT_GROUPS = ["Match Result", "Goals", "Double Chance", "Both Teams To Score", "Handicap"]


def fetch_fixture_board(
    fixture: Fixture,
    groups: list[str] | None = None,
    max_markets: int = 120,
) -> dict:
    """The SportyBet market board for one fixture, ready for the API.

    Never raises: any failure is returned as ``{"available": False, ...}`` so a
    book outage degrades to "board unavailable" instead of breaking the page.
    """
    if not settings.sportybet_markets_enabled:
        return {
            "available": False,
            "reason": "disabled",
            "message": (
                "The SportyBet market board is switched off. Set "
                "SPORTYBET_MARKETS_ENABLED=true to load live book prices."
            ),
        }

    kickoff = fixture.kickoff
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)

    try:
        # A late kickoff can be listed on the following UTC day.
        events = fetch_events_for_date(kickoff)
        event = find_event_for_fixture(fixture, events)
        if event is None:
            events = fetch_events_for_date(kickoff + timedelta(days=1))
            event = find_event_for_fixture(fixture, events)
        if event is None:
            return {
                "available": False,
                "reason": "no_matching_event",
                "message": (
                    "No SportyBet event matches this fixture's teams and kickoff. "
                    "GoalEdge's fixtures are simulated, so most will not line up "
                    "with a real match."
                ),
            }

        event_id = str(event.get("eventId") or event.get("id") or "")
        if not event_id:
            return {"available": False, "reason": "no_event_id", "message": "Malformed event."}

        # Named `fetched`, not `markets`, to avoid shadowing the imported
        # taxonomy module used further down this function.
        fetched = fetch_event_markets(event_id)
        if not fetched:
            return {
                "available": False,
                "reason": "no_markets",
                "message": "SportyBet listed no priced markets for this event.",
            }
    except SportyBetUnavailable as exc:
        return {"available": False, "reason": "unavailable", "message": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive, must not 500 the page
        return {"available": False, "reason": "error", "message": str(exc)}

    total = len(fetched)
    truncated = total > max_markets
    visible = fetched[:max_markets]
    grouped = group_markets(visible, groups)

    # The full category list, including categories the feed did not return for
    # this fixture, so the panel can show what the book offers in general and
    # mark which of those the engine can price.
    present = {g["group_key"] for g in grouped}
    categories = [
        {**cat, "present": cat["key"] in present} for cat in markets.category_payload()
    ]

    return {
        "available": True,
        "bookmaker": "SportyBet",
        "event_id": event_id,
        "source": "sportybet",
        "groups": grouped,
        "categories": categories,
        # CATEGORIES holds Category dataclass instances, so attribute access is
        # correct here (category_payload() is what returns dicts).
        "model_priced_categories": [c.key for c in markets.CATEGORIES if c.model_priced],
        "market_count": total,
        "shown_count": len(visible),
        "truncated": truncated,
        "caveat": (
            "Prices are SportyBet's, shown for reference only. GoalEdge prices only "
            "1X2, Over/Under and GG/NG — every other category carries no prediction, "
            "edge or confidence, and no recommendation of any kind. Odds move; "
            "verify on the book before staking."
        ),
    }
