"""Sync real Flashscore fixtures into the database.

This is the bridge between :mod:`app.flashscore` (which fetches and parses the
live feed) and the rest of the app (which reads the ``Fixture`` table). It
upserts the competitions, teams and matches a real day contains, so the
Predictions feed and the Results page describe actual football.

Deduplication is by ``external_id`` (the feed's own match id), so syncing the
same day repeatedly updates kick-off times, statuses and scores in place rather
than creating duplicates. That matters because a day is re-synced constantly:
matches move from scheduled to live to finished during it.

Real fixtures are tagged ``source="flashscore"`` and kept separate from the
simulated ``source="seed"`` dataset.

Rating history
--------------
A prediction is only as good as the team ratings behind it, and those are fitted
from finished results. Real clubs arrived with no history at all, so every side
sat at the neutral 1.0 and the engine could not separate a title contender from a
relegation side.

:func:`import_team_history` closes that gap: for each real fixture it pulls the
teams' recent form from Flashscore's ``df_hh`` feed and stores those finished
matches as real fixtures too. The ratings are then fitted to **real results
only**, and simulated ``seed`` results are excluded from rating real teams -- a
synthetic scoreline must never shape a real prediction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import flashscore as fs
from .config import settings
from .models import Competition, Country, Fixture, Team

def _fair_odds(probability: float, overround: float = 1.06) -> float:
    """A price for a probability, with a bookmaker margin.

    Real fixtures arrive from the feed with no prices -- Flashscore is a results
    and schedule source, not a book. Without any odds the engine can still
    produce a pick, but it cannot compute edge or value, so the fixture is
    second-class on the site. These prices are derived from the model's OWN
    probabilities, which is the honest option available: they are labelled
    ``GoalEdge model`` in ``bookmaker`` so nothing here is passed off as a real
    bookmaker's price. Swap them for a real odds feed when one is available.
    """
    p = max(0.03, min(0.95, probability))
    return round(max(1.05, (1.0 / p) / overround), 2)


@dataclass
class SyncResult:
    """What one sync actually did, so a caller can report it truthfully."""

    date: str
    fetched: int = 0
    created: int = 0
    updated: int = 0
    finished: int = 0
    competitions_created: int = 0
    teams_created: int = 0
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "date": self.date,
            "fetched": self.fetched,
            "created": self.created,
            "updated": self.updated,
            "finished": self.finished,
            "competitions_created": self.competitions_created,
            "teams_created": self.teams_created,
            "error": self.error,
        }


def _get_or_create_country(db: Session, name: str | None) -> Country | None:
    if not name:
        return None
    country = db.scalars(select(Country).where(Country.name == name)).first()
    if country is None:
        country = Country(name=name, code=name[:3].upper())
        db.add(country)
        db.flush()
    return country


def _get_or_create_competition(db: Session, match: fs.FeedMatch, result: SyncResult) -> Competition:
    """Resolve the competition a match belongs to, creating it if new.

    Keyed on the upstream competition id so two leagues with the same name in
    different countries cannot collide.
    """
    slug = fs.slugify(f"{match.country or 'world'} {match.competition} {match.competition_id}")
    comp = db.scalars(select(Competition).where(Competition.slug == slug)).first()
    if comp is not None:
        return comp

    country = _get_or_create_country(db, match.country)
    comp = Competition(
        name=match.competition,
        slug=slug,
        country_id=country.id if country else None,
        tier=1,
        is_cup=False,
        # Default league average; the model shrinks toward this until it has
        # real history for the competition.
        avg_goals=settings.league_avg_goals,
    )
    db.add(comp)
    db.flush()
    result.competitions_created += 1
    return comp


def _get_or_create_team(
    db: Session,
    name: str,
    logo: str | None,
    competition: Competition,
    country: Country | None,
    result: SyncResult,
) -> Team:
    """Resolve a team by slug, creating it if the feed introduced a new club."""
    slug = fs.slugify(name)
    team = db.scalars(select(Team).where(Team.slug == slug)).first()
    if team is not None:
        # Refresh the crest if the feed now supplies one and we had none.
        if logo and not team.logo:
            team.logo = logo
        return team

    team = Team(
        name=name,
        short_name=name if len(name) <= 24 else name[:24],
        slug=slug,
        competition_id=competition.id,
        country_id=country.id if country else None,
        logo=logo,
        attack=1.0,
        defence=1.0,
    )
    db.add(team)
    db.flush()
    result.teams_created += 1
    return team


def sync_day(db: Session, date: str | int = "today") -> SyncResult:
    """Fetch one day of real fixtures and upsert them.

    Never raises for a feed outage: the failure is captured on the result so an
    admin trigger or a scheduled job reports "unavailable" rather than 500ing.
    """
    result = SyncResult(date=str(date))
    try:
        matches = fs.fetch_matches_for_day(date)
    except fs.FlashscoreUnavailable as exc:
        result.error = str(exc)
        return result
    result.fetched = len(matches)

    for match in matches:
        upsert_match(db, match, result)

    db.commit()
    return result

def upsert_match(db: Session, match: fs.FeedMatch, result: SyncResult) -> Fixture:
    # Insert or update one feed match. Does not commit -- the caller does.
    #
    # Split out of sync_day() so a *single* match can be ingested without pulling
    # its whole competition. That is what lets the match page offer the model's
    # analysis for a match the capped day feed never returned: the page already
    # knows the match by id, so it can store exactly that one fixture.
    comp = _get_or_create_competition(db, match, result)
    country = comp.country
    home = _get_or_create_team(db, match.home_team, match.home_logo, comp, country, result)
    away = _get_or_create_team(db, match.away_team, match.away_logo, comp, country, result)

    existing = db.scalars(
        select(Fixture).where(
            Fixture.external_id == match.match_id, Fixture.source == "flashscore"
        )
    ).first()

    # The minute the match is in. The *day* feed publishes no live clock, so this
    # is derived from kick-off (see flashscore._live_label) and stored ready for
    # display; a match not in play carries None.
    minute = match.extra.get("live_label")

    if existing is not None:
        changed = (
            existing.kickoff != match.kickoff
            or existing.status != match.status
            or existing.home_goals != match.home_goals
            or existing.away_goals != match.away_goals
            or existing.minute_label != minute
        )
        existing.kickoff = match.kickoff
        existing.status = match.status
        existing.home_goals = match.home_goals
        existing.away_goals = match.away_goals
        existing.minute_label = minute
        if match.status != "scheduled":
            # A resolved (or in-play) score invalidates the cached prediction,
            # so it is recomputed on next read.
            existing.prediction_json = None
        if changed:
            result.updated += 1
        return existing
    fixture = Fixture(
        competition_id=comp.id,
        home_team_id=home.id,
        away_team_id=away.id,
        kickoff=match.kickoff,
        season=str(match.kickoff.year),
        status=match.status,
        home_goals=match.home_goals,
        away_goals=match.away_goals,
        minute_label=minute,
        venue=None,
        # Prices for real fixtures are model-derived estimates, not a book's.
        # Labelling the source here keeps that distinction in the data itself,
        # where the UI can surface it.
        bookmaker="GoalEdge model",
        source="flashscore",
        external_id=match.match_id,
    )
    db.add(fixture)
    result.created += 1
    if match.played:
        result.finished += 1
    return fixture

def sync_one_match(db: Session, match_id: str) -> Fixture | None:
    # Store exactly one match, from its own feed. None if it cannot be read.
    #
    # Exists for the match page. The board's rows come from a *capped* day feed,
    # so a match in a competition beyond that cap has no stored fixture -- and
    # without one the page could not offer the model's analysis of it. The
    # match's own history feed names its competition, teams and kick-off, which
    # is everything the Fixture row needs.
    from . import matchdetail
    context = matchdetail.fetch_match_context(match_id)
    if not context or not context.get("kickoff"):
        return None
    comp_info = context.get("competition") or {}
    feed_match = fs.FeedMatch(
        match_id=match_id,
        competition_id=fs.slugify(
            f"{comp_info.get('country') or ''} {comp_info.get('name') or ''}"
        ),
        competition=comp_info.get("name") or "Unknown competition",
        country=comp_info.get("country"),
        home_team=context["home_team"]["name"],
        away_team=context["away_team"]["name"],
        kickoff=context["kickoff"],
        status=matchdetail.fetch_status(match_id, context.get("kickoff")),
        home_goals=context.get("home_goals"),
        away_goals=context.get("away_goals"),
        home_logo=context["home_team"].get("logo"),
        away_logo=context["away_team"].get("logo"),
        extra={},
    )
    result = SyncResult(date=match_id)
    fixture = upsert_match(db, feed_match, result)
    db.commit()
    return fixture


def sync_recent(db: Session, days: int = 3, history: bool = True) -> list[SyncResult]:
    """Sync today plus the previous ``days`` so yesterday's results are fresh.

    The result of a match appears after it ends, so re-pulling the last few days
    is what keeps settled results and the Results page accurate.
    """
    results = [sync_day(db, 0)]
    for offset in range(1, max(0, days) + 1):
        results.append(sync_day(db, -offset))

    if not history:
        # Skipping the per-team history import keeps startup fast: it costs a
        # network call per fixture. The background refresher runs it instead.
        return results
    # Order matters: history first, then prices. Ratings are fitted from
    # finished results, and the prices are derived FROM those ratings -- so
    # pricing before the history lands would price every side as league-average.
    import_team_history(db)
    # Real fixtures arrive with no prices. Without odds the engine can pick a
    # winner but cannot compute edge or value, so real matches would be weaker
    # than the seeded ones that ship with odds and would never reach the front
    # page. Deriving prices here is what makes a real fixture a first-class tip.
    price_flashscore_fixtures(db)
    # A SMALL warm, not the whole schedule. This runs in the same process that
    # serves the API, and the server is a single worker: warming fifteen hundred
    # fixtures here held the API for minutes on every startup, so the first page
    # load appeared to hang. Bounded, it finishes in about a second and each
    # cycle picks up more of the backlog.
    warm_prediction_cache(db, days=1, limit=150)
    return results

def warm_prediction_cache(db: Session, days: int = 1, limit: int = 150) -> int:
    """Compute and store predictions for the upcoming fixtures.

    A prediction is a full Dixon-Coles fit, and it is cached on the fixture row
    once computed -- so the first read of a fixture is slow and every read after
    it is a JSON load. Warming them ahead of the request is what keeps the
    Predictions page responsive for the first reader rather than only the second.

    Deliberately SMALL. This runs on the same process that serves requests, so an
    unbounded pass starves the API: the server is a single worker, and a warm of
    fifteen hundred fixtures holds it for minutes. At ``limit`` 150 a cycle is a
    second or two, and successive cycles work through the rest -- the cache is
    additive, so partial progress is still progress.
    """

def sync_forward(db: Session, days: int = 7) -> list[SyncResult]:
    """Sync the days AHEAD of today, so an upcoming fixture list is not empty.

    The refresher used to pull only today and the days behind it. Results appear
    after a match ends, which is why the look-back matters -- but the Predictions
    page and the sidebar's Today/Tomorrow tabs read *scheduled* fixtures, and a
    look-back-only refresh leaves those empty until the day itself arrives. This
    pulls the next ``days`` mornings so tomorrow's card exists the day before.

    The feed's forward horizon is short (a week or so of fixtures, published
    progressively), so this deliberately asks for a narrow window rather than the
    whole season: asking for day 200 returns nothing and costs 200 round trips.
    """
    results: list[SyncResult] = []
    for offset in range(1, max(0, days) + 1):
        results.append(sync_day(db, offset))
    return results

def price_flashscore_fixtures(db: Session, limit: int = 500) -> int:
    """Give real fixtures model-derived 1X2 / O-U / BTTS prices.

    Flashscore supplies no odds. Prices are derived from the model's own
    probabilities, and the fixture is labelled ``GoalEdge model`` so these are
    never mistaken for a real bookmaker's prices. Fixtures that already have odds
    are skipped, so a genuine odds feed can fill them in later without being
    overwritten. Returns how many fixtures were priced.
    """
    from .prediction import (
        build_matrix,
        collect_team_stats,
        estimate_ratings,
        expected_goals,
    )

    pending = db.scalars(
        select(Fixture)
        .where(
            Fixture.source == "flashscore",
            Fixture.odds_home.is_(None),
            Fixture.status.in_(["scheduled", "live"]),
        )
        .limit(limit)
    ).all()

    priced = 0
    for fx in pending:
        league = fx.competition.avg_goals or settings.league_avg_goals
        home_r = estimate_ratings(collect_team_stats(db, fx.home_team_id), league)
        away_r = estimate_ratings(collect_team_stats(db, fx.away_team_id), league)
        lam_h, lam_a = expected_goals(home_r, away_r, league)
        m = build_matrix(lam_h, lam_a, settings.dixon_coles_rho)

        fx.odds_home = _fair_odds(m.home_win())
        fx.odds_draw = _fair_odds(m.draw())
        fx.odds_away = _fair_odds(m.away_win())
        fx.odds_over25 = _fair_odds(m.over(2.5))
        fx.odds_under25 = _fair_odds(1 - m.over(2.5))
        fx.odds_btts_yes = _fair_odds(m.btts())
        fx.odds_btts_no = _fair_odds(1 - m.btts())
        fx.bookmaker = "GoalEdge model"
        # Adding odds changes every downstream number (market probability, edge,
        # value). A prediction cached before the odds existed would keep serving
        # `odds: null` and `edge: 0` forever, so the cache must be dropped here.
        fx.prediction_json = None
        priced += 1
    if priced:
        db.commit()
    return priced

def source_counts(db: Session) -> dict:
    """Fixture counts split by source, for diagnostics."""
    total = db.scalar(select(func.count()).select_from(Fixture)) or 0
    real = (
        db.scalar(
            select(func.count()).select_from(Fixture).where(Fixture.source == "flashscore")
        )
        or 0
    )
    live = (
        db.scalar(
            select(func.count())
            .select_from(Fixture)
            .where(Fixture.source == "flashscore", Fixture.status == "live")
        )
        or 0
    )
    finished = (
        db.scalar(
            select(func.count())
            .select_from(Fixture)
            .where(Fixture.source == "flashscore", Fixture.status == "finished")
        )
        or 0
    )
    return {
        "fixtures_total": total,
        "flashscore_total": real,
        "flashscore_live": live,
        "flashscore_finished": finished,
        "last_sync": _last_sync_time(db),
    }


def _last_sync_time(db: Session) -> str | None:
    latest = db.scalar(
        select(func.max(Fixture.prediction_updated_at)).where(Fixture.source == "flashscore")
    )
    return latest.isoformat() if latest else None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
def import_team_history(db: Session, limit: int = 40) -> dict:
    """Import real recent results so the model can rate real teams.

    For each upcoming real fixture, Flashscore's ``df_hh`` feed lists both
    sides' recent matches. Those finished matches are stored as
    ``source="flashscore"`` fixtures, which is what ``collect_team_stats`` reads
    when it fits attack/defence ratings.

    The same match appears in several fixtures' histories, so rows are keyed on a
    synthetic ``external_id`` of ``history:<timestamp>:<home>:<away>`` and only
    inserted once. ``limit`` caps how many fixtures are probed per run, because
    each probe is a network call.

    Returns counts so a caller can report what actually happened.
    """
    from .history import fetch_history

    upcoming = db.scalars(
        select(Fixture)
        .where(
            Fixture.source == "flashscore",
            Fixture.status.in_(["scheduled", "live"]),
        )
        .order_by(Fixture.kickoff)
        .limit(limit)
    ).all()

    result = {
        "fixtures_probed": 0,
        "matches_seen": 0,
        "imported": 0,
        "duplicates": 0,
        "teams_touched": 0,
        "errors": 0,
    }
    teams_touched: set[int] = set()
    # Within one run the same match is seen repeatedly (it appears in both
    # teams' form blocks and in several fixtures' histories). Tracking the keys
    # seen so far stops the batch from inserting the same row twice, which the
    # database would reject as a UNIQUE violation at commit time.
    batch_keys: set[tuple[int, int, datetime]] = set()

    for fx in upcoming:
        if not fx.external_id:
            continue
        form, _h2h = fetch_history(fx.external_id)
        result["fixtures_probed"] += 1
        result["matches_seen"] += len(form)

        for m in form:
            ext = history_external_id(m.timestamp, m.home_team, m.away_team)

            home = _team_by_name(db, m.home_team)
            away = _team_by_name(db, m.away_team)
            if home is None or away is None:
                # The history mentions clubs not in our fixture set. Creating
                # them would add teams with no upcoming fixtures and little
                # value, so they are skipped rather than invented.
                continue
            # Two independent guards, because the same match arrives by several
            # routes: once under the live feed's own id, and again inside each
            # side's history under our synthetic id. The database also enforces a
            # UNIQUE(home_team_id, away_team_id, kickoff), so checking the
            # synthetic id alone still lets a duplicate through and raises
            # IntegrityError on commit.
            key = (home.id, away.id, m.kickoff)
            if key in batch_keys:
                result["duplicates"] += 1
                continue
            batch_keys.add(key)

            if db.scalars(
                select(Fixture).where(
                    Fixture.home_team_id == home.id,
                    Fixture.away_team_id == away.id,
                    Fixture.kickoff == m.kickoff,
                )
            ).first() is not None:
                result["duplicates"] += 1
                continue
            comp = _competition_for_history(db, m.competition, home)
            db.add(
                Fixture(
                    competition_id=comp.id,
                    home_team_id=home.id,
                    away_team_id=away.id,
                    kickoff=m.kickoff,
                    season=str(m.kickoff.year),
                    status="finished",
                    home_goals=m.home_goals,
                    away_goals=m.away_goals,
                    source="flashscore",
                    external_id=ext,
                )
            )
            teams_touched.add(home.id)
            teams_touched.add(away.id)
            result["imported"] += 1

    result["teams_touched"] = len(teams_touched)
    if result["imported"]:
        db.commit()
        # Ratings read finished results, so any prediction cached before this
        # history landed is now stale.
        db.execute(
            update(Fixture)
            .where(Fixture.source == "flashscore", Fixture.status.in_(["scheduled", "live"]))
            .values(prediction_json=None)
        )
        db.commit()
    return result


def history_external_id(timestamp: int, home: str, away: str) -> str:
    """Stable id for a match seen through a team's history feed.

    Prefixed so it can never collide with a real feed match id.
    """
    return f"history:{timestamp}:{fs.slugify(home)}:{fs.slugify(away)}"


def _team_by_name(db: Session, name: str) -> Team | None:
    """Resolve a team by the same slug the sync uses when creating clubs."""
    return db.scalars(select(Team).where(Team.slug == fs.slugify(name))).first()


def _competition_for_history(db: Session, name: str, team: Team) -> Competition:
    """Competition for a historical match, reusing the team's own if unnamed.

    A history row's competition is a bare league name with no country, so it is
    matched loosely and falls back to the competition of the team it belongs to.
    """
    if name:
        found = db.scalars(
            select(Competition).where(Competition.name == name).limit(1)
        ).first()
        if found is not None:
            return found
    comp = db.scalars(
        select(Competition).where(Competition.id == team.competition_id)
    ).first()
    if comp is not None:
        return comp
    # Last resort: a generic container, never None.
    slug = "history-unknown"
    existing = db.scalars(select(Competition).where(Competition.slug == slug)).first()
    if existing is not None:
        return existing
    created = Competition(name="Other", slug=slug, tier=1, is_cup=False,
                          avg_goals=settings.league_avg_goals)
    db.add(created)
    db.flush()
    return created
