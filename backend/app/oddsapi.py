"""The Odds API — real bookmaker prices overlaid onto Flashscore fixtures.

Flashscore has no odds. Until a book feed writes ``fixtures.odds_*``, the
engine labels prices ``GoalEdge model`` and refuses to claim an edge (see
:mod:`app.pricing`). This module is that book feed:

1. Map each upcoming real competition onto an Odds API ``sport`` key.
2. Fetch 1X2 (and totals when the books publish them) for those sports.
3. Match events onto fixtures by canonical team names + kick-off window.
4. Write consensus (median) decimal odds and a real ``bookmaker`` label.

Matching is strict after canonicalisation. A near-miss shows no book rather
than another match's prices.

Quota
-----
The free Odds API tier is a few hundred credits a month. Each sport × region
is one credit. Sports are therefore fetched only when we already have an
upcoming Flashscore fixture in that league, cached for hours, and capped per
run. Burning the quota on boot would silently disable value detection for
the rest of the month.
"""
from __future__ import annotations

import json
import logging
import statistics
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .config import settings
from .models import Competition, Country, Fixture, Team
from .pricing import is_model_priced
from .sportybet import normalise_team_name

log = logging.getLogger("goaledge.oddsapi")

#: In-process cache: sport_key -> (fetched_at, events, remaining).
_CACHE: dict[str, tuple[datetime, list[dict], str | None]] = {}

#: (sport_key, countries, needles, excludes). Countries empty = any country.
#: More specific names first so "Bundesliga 2" does not fall through to
#: "Bundesliga", and "Serie B" does not become Serie A. Country is required
#: for generic names ("Premier League", "Serie A") so Jamaica PL is not EPL.
_SPORT_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...] = (
    ("soccer_germany_bundesliga2", ("germany",), ("bundesliga 2", "2. bundesliga", "2 bundesliga"), ()),
    ("soccer_germany_liga3", ("germany",), ("3. liga", "3 liga"), ()),
    ("soccer_germany_dfb_pokal", ("germany",), ("dfb pokal", "dfb-pokal"), ()),
    ("soccer_germany_bundesliga", ("germany",), ("bundesliga",), ("bundesliga 2", "2. bundesliga", "3. liga")),
    ("soccer_italy_serie_b", ("italy",), ("serie b",), ()),
    ("soccer_italy_serie_a", ("italy",), ("serie a",), ()),
    ("soccer_france_ligue_two", ("france",), ("ligue 2",), ()),
    ("soccer_france_ligue_one", ("france",), ("ligue 1",), ()),
    ("soccer_spain_segunda_division", ("spain",), ("segunda", "laliga2", "la liga 2"), ()),
    ("soccer_spain_la_liga", ("spain",), ("la liga", "laliga", "primera"), ("segunda", "2")),
    ("soccer_efl_champ", ("england",), ("championship",), ("u18", "u21")),
    ("soccer_epl", ("england",), ("premier league", "epl"), ("premier league 2", "u18", "u21", "international")),
    ("soccer_england_league1", ("england",), ("league one", "league 1"), ("premier league",)),
    ("soccer_england_league2", ("england",), ("league two", "league 2"), ("premier league",)),
    ("soccer_england_efl_cup", ("england",), ("efl cup", "carabao", "league cup"), ("women",)),
    ("soccer_spl", ("scotland",), ("premiership", "scottish premier"), ()),
    ("soccer_uefa_champs_league", (), ("champions league", "uefa champions"), ("women", "youth", "u19")),
    ("soccer_uefa_europa_league", (), ("europa league",), ("conference", "women")),
    ("soccer_uefa_europa_conference_league", (), ("conference league", "europa conference"), ("women",)),
    ("soccer_uefa_nations_league", (), ("nations league",), ()),
    ("soccer_netherlands_eredivisie", ("netherlands",), ("eredivisie",), ()),
    ("soccer_portugal_primeira_liga", ("portugal",), ("primeira liga", "liga portugal"), ()),
    ("soccer_belgium_first_div", ("belgium",), ("jupiler", "pro league", "first division a"), ()),
    ("soccer_turkey_super_league", ("turkey",), ("super lig", "süper lig", "superlig"), ()),
    ("soccer_greece_super_league", ("greece",), ("super league",), ()),
    ("soccer_austria_bundesliga", ("austria",), ("bundesliga", "admiral"), ()),
    ("soccer_switzerland_superleague", ("switzerland",), ("super league", "swiss super"), ()),
    ("soccer_denmark_superliga", ("denmark",), ("superliga",), ()),
    ("soccer_sweden_allsvenskan", ("sweden",), ("allsvenskan",), ()),
    ("soccer_norway_eliteserien", ("norway",), ("eliteserien",), ()),
    ("soccer_poland_ekstraklasa", ("poland",), ("ekstraklasa",), ()),
    ("soccer_russia_premier_league", ("russia",), ("premier liga", "premier league"), ()),
    ("soccer_usa_mls", ("usa", "united states"), ("major league soccer", "mls"), ()),
    ("soccer_mexico_ligamx", ("mexico",), ("liga mx",), ()),
    ("soccer_brazil_serie_b", ("brazil",), ("serie b", "série b"), ()),
    ("soccer_brazil_campeonato", ("brazil",), ("serie a", "série a", "brasileirao", "brasileirão"), ()),
    ("soccer_argentina_primera_division", ("argentina",), ("liga profesional", "primera"), ()),
    ("soccer_conmebol_copa_libertadores", (), ("libertadores",), ("women",)),
    ("soccer_conmebol_copa_sudamericana", (), ("sudamericana",), ()),
    ("soccer_australia_aleague", ("australia",), ("a-league", "a league"), ()),
    ("soccer_japan_j_league", ("japan",), ("j1", "j-league", "j league"), ()),
    ("soccer_korea_kleague1", ("korea",), ("k league", "k-league"), ()),
    ("soccer_saudi_arabia_pro_league", ("saudi",), ("saudi pro", "roshn", "pro league"), ()),
    ("soccer_china_superleague", ("china",), ("super league", "chinese super"), ()),
    ("soccer_league_of_ireland", ("ireland",), ("premier division",), ()),
    ("soccer_finland_veikkausliiga", ("finland",), ("veikkausliiga",), ()),
)

#: Country + competition name used when an Odds API event has no Flashscore row.
_SPORT_COMP: dict[str, tuple[str, str]] = {
    "soccer_epl": ("England", "Premier League"),
    "soccer_spain_la_liga": ("Spain", "LaLiga"),
    "soccer_italy_serie_a": ("Italy", "Serie A"),
    "soccer_germany_bundesliga": ("Germany", "Bundesliga"),
    "soccer_france_ligue_one": ("France", "Ligue 1"),
    "soccer_uefa_champs_league": ("Europe", "Champions League"),
    "soccer_uefa_europa_league": ("Europe", "Europa League"),
    "soccer_efl_champ": ("England", "Championship"),
}

#: Fetched even when Flashscore has no mapped upcoming row — otherwise a lag
#: in the schedule feed would leave the book overlay with nothing to do.
DEFAULT_SPORTS: tuple[str, ...] = (
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_efl_champ",
)

#: After :func:`normalise_team_name`, map feed-specific spellings onto one key.
#: Only identities, never two different clubs.
_CANON: dict[str, str] = {
    "man utd": "manchester united",
    "man united": "manchester united",
    "manchester utd": "manchester united",
    "manchester united fc": "manchester united",
    "man city": "manchester city",
    "manchester city fc": "manchester city",
    "spurs": "tottenham",
    "tottenham hotspur": "tottenham",
    "wolves": "wolverhampton",
    "wolverhampton wanderers": "wolverhampton",
    "nottingham forest": "nottingham forest",
    "nottm forest": "nottingham forest",
    "notts forest": "nottingham forest",
    "brighton and hove albion": "brighton",
    "brighton hove albion": "brighton",
    "west ham united": "west ham",
    "newcastle united": "newcastle",
    "leicester city": "leicester",
    "leeds united": "leeds",
    "ipswich town": "ipswich",
    "sheffield utd": "sheffield united",
    "sheff utd": "sheffield united",
    "sheff wed": "sheffield wednesday",
    "sheffield wednesday": "sheffield wednesday",
    "psg": "paris saint germain",
    "paris sg": "paris saint germain",
    "paris saint germain": "paris saint germain",
    "inter milan": "inter",
    "internazionale": "inter",
    "fc internazionale": "inter",
    "atletico de madrid": "atletico madrid",
    "atletico madrid": "atletico madrid",
    "athletic club": "athletic bilbao",
    "athletic bilbao": "athletic bilbao",
    "bayern munchen": "bayern munich",
    "fc bayern munich": "bayern munich",
    "bayern munich": "bayern munich",
    "koln": "cologne",
    "1 koln": "cologne",
    "sporting cp": "sporting",
    "sporting lisbon": "sporting",
    "eintracht frankfurt": "frankfurt",
    "borussia dortmund": "dortmund",
    "borussia monchengladbach": "monchengladbach",
    "gladbach": "monchengladbach",
    "m gladbach": "monchengladbach",
    "rb leipzig": "leipzig",
    "rasenballsport leipzig": "leipzig",
    "tsg hoffenheim": "hoffenheim",
    "mainz 05": "mainz",
    "fc cologne": "cologne",
    "st pauli": "st pauli",
    "union berlin": "union berlin",
    "real betis": "betis",
    "real sociedad": "real sociedad",
    "celta vigo": "celta",
    "rc celta": "celta",
    "deportivo alaves": "alaves",
    "hellas verona": "verona",
    "internazionale milano": "inter",
    "ac milan": "milan",
    "as roma": "roma",
    "ss lazio": "lazio",
    "olympique marseille": "marseille",
    "olympique lyonnais": "lyon",
    "ogc nice": "nice",
    "losc lille": "lille",
    "stade rennais": "rennes",
    "stade brestois": "brest",
    "stade de reims": "reims",
}


def canonical_team(name: str | None) -> str:
    """Stable identity for matching Odds API names onto Flashscore names."""
    n = normalise_team_name(name)
    return _CANON.get(n, n)


def sport_key_for(competition: Competition | None) -> str | None:
    """Odds API sport key for a competition, or None if we do not cover it."""
    if competition is None:
        return None
    country = ((competition.country.name if competition.country else "") or "").lower()
    haystack = f"{country} {competition.name}".lower()
    for key, countries, needles, excludes in _SPORT_RULES:
        if countries and not any(c in haystack for c in countries):
            continue
        if excludes and any(x in haystack for x in excludes):
            continue
        if any(n in haystack for n in needles):
            return key
    return None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return round(float(statistics.median(values)), 2)


def _parse_kickoff(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def extract_consensus(event: dict) -> dict[str, Any]:
    """Median decimal prices across books on one Odds API event.

    Totals other than 2.5 and books that only list 1X2 are skipped per-market
    rather than failing the whole event: a fixture can still get a real 1X2
    line when nobody prices Over 2.5.
    """
    home = event.get("home_team") or ""
    away = event.get("away_team") or ""
    homes: list[float] = []
    draws: list[float] = []
    aways: list[float] = []
    overs: list[float] = []
    unders: list[float] = []
    books: list[str] = []

    for bm in event.get("bookmakers") or []:
        if not isinstance(bm, dict):
            continue
        key = bm.get("key") or bm.get("title")
        if key:
            books.append(str(key))
        for market in bm.get("markets") or []:
            if not isinstance(market, dict):
                continue
            mkey = market.get("key")
            outcomes = market.get("outcomes") or []
            if mkey == "h2h":
                for o in outcomes:
                    if not isinstance(o, dict):
                        continue
                    price = o.get("price")
                    try:
                        price_f = float(price)
                    except (TypeError, ValueError):
                        continue
                    if price_f <= 1.0:
                        continue
                    name = o.get("name") or ""
                    if name == "Draw":
                        draws.append(price_f)
                    elif name == home:
                        homes.append(price_f)
                    elif name == away:
                        aways.append(price_f)
            elif mkey == "totals":
                for o in outcomes:
                    if not isinstance(o, dict):
                        continue
                    if o.get("point") != 2.5:
                        continue
                    try:
                        price_f = float(o.get("price"))
                    except (TypeError, ValueError):
                        continue
                    if price_f <= 1.0:
                        continue
                    label = (o.get("name") or "").lower()
                    if label == "over":
                        overs.append(price_f)
                    elif label == "under":
                        unders.append(price_f)

    preferred = next(
        (b for b in ("pinnacle", "betfair_ex_uk", "williamhill", "bet365") if b in books),
        books[0] if books else None,
    )
    label = "Odds API"
    if preferred:
        label = f"{preferred} (Odds API)"
    return {
        "home_team": home,
        "away_team": away,
        "commence_time": event.get("commence_time"),
        "odds_home": _median(homes),
        "odds_draw": _median(draws),
        "odds_away": _median(aways),
        "odds_over25": _median(overs),
        "odds_under25": _median(unders),
        "bookmaker": label,
        "books": books,
        "has_1x2": None not in (_median(homes), _median(draws), _median(aways)),
    }


def names_match(event_home: str, event_away: str, fx_home: str, fx_away: str) -> bool:
    return (
        canonical_team(event_home) == canonical_team(fx_home)
        and canonical_team(event_away) == canonical_team(fx_away)
    )


def kickoff_close(event_kickoff: datetime | None, fixture_kickoff: datetime | None, hours: float) -> bool:
    if event_kickoff is None or fixture_kickoff is None:
        return False
    a = event_kickoff if event_kickoff.tzinfo else event_kickoff.replace(tzinfo=timezone.utc)
    b = fixture_kickoff if fixture_kickoff.tzinfo else fixture_kickoff.replace(tzinfo=timezone.utc)
    return abs((a - b).total_seconds()) <= hours * 3600


def _get_json(path: str, params: dict[str, str]) -> tuple[Any, dict[str, str]]:
    query = urllib.parse.urlencode(params)
    url = f"{settings.odds_api_base_url.rstrip('/')}/{path.lstrip('/')}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "GoalEdge/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=settings.odds_api_timeout_seconds) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return json.loads(resp.read().decode()), headers
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"Odds API HTTP {exc.code}: {body}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Odds API unreachable: {exc.reason}") from None


def fetch_sport_odds(sport_key: str, *, force: bool = False) -> tuple[list[dict], str | None]:
    """Upcoming events + odds for one sport. Cached. One credit per call."""
    now = datetime.now(timezone.utc)
    cached = _CACHE.get(sport_key)
    ttl = timedelta(seconds=settings.odds_api_cache_seconds)
    if cached and not force and now - cached[0] < ttl:
        return cached[1], cached[2]

    if not settings.odds_api_key:
        raise RuntimeError("ODDS_API_KEY is not set")

    payload, headers = _get_json(
        f"/sports/{sport_key}/odds",
        {
            "apiKey": settings.odds_api_key,
            "regions": settings.odds_api_regions,
            "markets": settings.odds_api_markets,
            "oddsFormat": "decimal",
        },
    )
    events = payload if isinstance(payload, list) else []
    remaining = headers.get("x-requests-remaining")
    _CACHE[sport_key] = (now, events, remaining)
    log.info(
        "odds api: %s %s event(s), remaining=%s",
        sport_key,
        len(events),
        remaining,
    )
    return events, remaining


def _upcoming(db: Session) -> list[Fixture]:
    horizon = datetime.now(timezone.utc) + timedelta(days=14)
    return list(
        db.scalars(
            select(Fixture)
            .options(
                selectinload(Fixture.home_team),
                selectinload(Fixture.away_team),
                selectinload(Fixture.competition).selectinload(Competition.country),
            )
            .where(
                Fixture.source != "seed",
                Fixture.status.in_(["scheduled", "live"]),
                Fixture.kickoff <= horizon,
            )
        ).all()
    )


def _sports_needed(fixtures: list[Fixture]) -> list[str]:
    """Sports to fetch this run: mapped upcoming leagues, then the defaults."""
    seen: list[str] = []
    for fx in fixtures:
        key = sport_key_for(fx.competition)
        if key and key not in seen:
            seen.append(key)
    for key in DEFAULT_SPORTS:
        if key not in seen:
            seen.append(key)
    cap = settings.odds_api_max_sports_per_run
    return seen[: cap]


def match_event(event: dict, fixtures: list[Fixture]) -> Fixture | None:
    home = event.get("home_team") or ""
    away = event.get("away_team") or ""
    kickoff = _parse_kickoff(event.get("commence_time"))
    slack = settings.odds_api_kickoff_slack_hours
    hits = [
        fx
        for fx in fixtures
        if names_match(home, away, fx.home_team.display_name, fx.away_team.display_name)
        and kickoff_close(kickoff, fx.kickoff, slack)
    ]
    if len(hits) == 1:
        return hits[0]
    # Unique name pair even if kick-off is published a few hours apart.
    named = [
        fx
        for fx in fixtures
        if names_match(home, away, fx.home_team.display_name, fx.away_team.display_name)
    ]
    if len(named) == 1 and kickoff_close(kickoff, named[0].kickoff, max(slack, 24)):
        return named[0]
    return None


def _get_or_create_country(db: Session, name: str) -> Country:
    row = db.scalars(select(Country).where(Country.name == name)).first()
    if row is None:
        row = Country(name=name, code=name[:3].upper())
        db.add(row)
        db.flush()
    return row


def _get_or_create_competition(db: Session, sport_key: str) -> Competition:
    from .flashscore import slugify

    country_name, comp_name = _SPORT_COMP.get(sport_key, ("World", sport_key))
    country = _get_or_create_country(db, country_name)
    slug = slugify(f"{country_name} {comp_name} oddsapi")
    # Prefer an existing Flashscore/seed row with the same country + name so
    # we do not split one league across two competition ids.
    existing = db.scalars(
        select(Competition).where(
            Competition.name == comp_name,
            Competition.country_id == country.id,
        )
    ).first()
    if existing is not None:
        return existing
    by_slug = db.scalars(select(Competition).where(Competition.slug == slug)).first()
    if by_slug is not None:
        return by_slug
    comp = Competition(
        name=comp_name,
        slug=slug,
        country_id=country.id,
        tier=1,
        is_cup="cup" in comp_name.lower() or "champions" in comp_name.lower() or "europa" in comp_name.lower(),
        avg_goals=settings.league_avg_goals,
    )
    db.add(comp)
    db.flush()
    return comp


def _resolve_team(db: Session, name: str, competition: Competition) -> Team:
    from .flashscore import slugify

    canon = canonical_team(name)
    in_comp = db.scalars(select(Team).where(Team.competition_id == competition.id)).all()
    for team in in_comp:
        if canonical_team(team.name) == canon or canonical_team(team.display_name) == canon:
            return team
    slug = slugify(name)
    by_slug = db.scalars(select(Team).where(Team.slug == slug)).first()
    if by_slug is not None:
        return by_slug
    team = Team(
        name=name,
        short_name=name if len(name) <= 24 else name[:24],
        slug=slug,
        competition_id=competition.id,
        country_id=competition.country_id,
        attack=1.0,
        defence=1.0,
    )
    db.add(team)
    db.flush()
    return team


def ingest_event(db: Session, sport_key: str, event: dict, kickoff: datetime) -> Fixture:
    """Create (or reuse) a fixture for an Odds API event Flashscore did not have."""
    event_id = str(event.get("id") or "")
    external_id = f"odds:{event_id}" if event_id else None
    if external_id:
        existing = db.scalars(
            select(Fixture)
            .options(
                selectinload(Fixture.home_team),
                selectinload(Fixture.away_team),
                selectinload(Fixture.competition).selectinload(Competition.country),
            )
            .where(Fixture.external_id == external_id)
        ).first()
        if existing is not None:
            return existing
    competition = _get_or_create_competition(db, sport_key)
    home = _resolve_team(db, event.get("home_team") or "Home", competition)
    away = _resolve_team(db, event.get("away_team") or "Away", competition)
    pair = db.scalars(
        select(Fixture)
        .options(
            selectinload(Fixture.home_team),
            selectinload(Fixture.away_team),
            selectinload(Fixture.competition).selectinload(Competition.country),
        )
        .where(
            Fixture.home_team_id == home.id,
            Fixture.away_team_id == away.id,
            Fixture.kickoff == kickoff,
        )
    ).first()
    if pair is not None:
        return pair
    fixture = Fixture(
        competition_id=competition.id,
        home_team_id=home.id,
        away_team_id=away.id,
        kickoff=kickoff,
        status="scheduled",
        source="oddsapi",
        external_id=external_id,
        bookmaker=None,
    )
    db.add(fixture)
    db.flush()
    # Relationships used by match_event / display_name immediately after.
    fixture.home_team = home
    fixture.away_team = away
    fixture.competition = competition
    return fixture


def apply_event_odds(fixture: Fixture, consensus: dict[str, Any]) -> bool:
    """Write consensus odds onto a fixture. Returns True if 1X2 landed."""
    if not consensus.get("has_1x2"):
        return False
    fixture.odds_home = consensus["odds_home"]
    fixture.odds_draw = consensus["odds_draw"]
    fixture.odds_away = consensus["odds_away"]
    if consensus.get("odds_over25") is not None:
        fixture.odds_over25 = consensus["odds_over25"]
    if consensus.get("odds_under25") is not None:
        fixture.odds_under25 = consensus["odds_under25"]
    fixture.bookmaker = consensus.get("bookmaker") or "Odds API"
    fixture.prediction_json = None
    return True


def apply_book_odds(db: Session, *, force: bool = False) -> dict:
    """Match Odds API events onto upcoming real fixtures and write prices.

    Flashscore often lags the book (or never lists a league). Unmatched events
    in a known sport are ingested as ``source=oddsapi`` fixtures so the
    Predictions page still has a real line to stand behind.

    Overwrites ``GoalEdge model`` prices; never overwrites a different real
    book unless ``force`` (used by the refresher so stale lines move).
    """
    if not settings.odds_api_key:
        return {"skipped": True, "reason": "ODDS_API_KEY is not set"}

    fixtures = _upcoming(db)
    sports = _sports_needed(fixtures)
    result = {
        "skipped": False,
        "fixtures": len(fixtures),
        "sports": sports,
        "fetched_events": 0,
        "matched": 0,
        "created": 0,
        "updated": 0,
        "unmatched_events": 0,
        "errors": [],
        "remaining": None,
    }
    if not sports:
        result["reason"] = "no mapped competitions in the upcoming window"
        return result

    for sport in sports:
        try:
            events, remaining = fetch_sport_odds(sport, force=force)
        except Exception as exc:  # noqa: BLE001 - one sport must not abort the rest
            result["errors"].append(f"{sport}: {exc}")
            log.warning("odds api: %s failed: %s", sport, exc)
            continue
        result["fetched_events"] += len(events)
        result["remaining"] = remaining
        for event in events:
            fx = match_event(event, fixtures)
            if fx is None:
                kickoff = _parse_kickoff(event.get("commence_time"))
                if kickoff is None:
                    result["unmatched_events"] += 1
                    continue
                try:
                    fx = ingest_event(db, sport, event, kickoff)
                    fixtures.append(fx)
                    result["created"] += 1
                except Exception as exc:  # noqa: BLE001 - one event must not abort the sport
                    result["unmatched_events"] += 1
                    result["errors"].append(f"{sport} ingest: {exc}")
                    continue
            else:
                result["matched"] += 1
            ours = (
                is_model_priced(fx.bookmaker)
                or not fx.bookmaker
                or "Odds API" in (fx.bookmaker or "")
            )
            if not ours and not force:
                continue
            consensus = extract_consensus(event)
            if apply_event_odds(fx, consensus):
                result["updated"] += 1

    if result["updated"] or result["created"]:
        db.commit()
    return result
