"""All REST endpoints for GoalEdge AI."""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload
from . import ai as ai_layer
from .config import settings
from .database import get_db
from . import design as site_design
from .models import (
    AdminAuditLog,
    Competition,
    Country,
    Fixture,
    Favourite,
    SiteSetting,
    Standing,
    Team,
    TrackedTip,
    User,
)
from . import flashscore
from . import markets as market_taxonomy
from . import matchdetail
from . import sportybet as sportybet_feed
from . import sync as fixture_sync
from .prediction import (
    OU_LINES,
    MAIN_OU_LINE,
    apply_result_to_tips,
    collect_team_stats,
    estimate_ratings,
    fixture_ou_odds,
    flat_selections,
    grade_prediction,
    implied_probability,
    kelly_fraction,
    ou_market_key,
    ou_market_name,
    predict_fixture,
    resolve_ou_line,
)
from .schemas import (
    AdminActionIn,
    AdminActionResult,
    AdminAuditOut,
    AdminRoleIn,
    AdminStatsOut,
    AdminUserListOut,
    AdminUserOut,
    AiAnalysisOut,
    CompetitionOut,
    CountryOut,
    FixtureListResponse,
    FixtureOut,
    OuLineOut,
    SiteDesignIn,
    SiteDesignOut,
    SiteDesignTemplatesOut,
    StatsOverviewOut,
    TeamOut,
    TipRecordOut,
    TokenOut,
    UserCreate,
    UserLogin,
    UserOut,
)
from .security import (
    create_access_token,
    get_current_admin,
    get_current_user,
    get_optional_user,
    hash_password,
    verify_password,
)

router = APIRouter()


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _fixture_query():
    return select(Fixture).options(
        selectinload(Fixture.competition).selectinload(Competition.country),
        selectinload(Fixture.home_team),
        selectinload(Fixture.away_team),
    )


def _upcoming_filter(now: datetime | None = None):
    """The WHERE clause for "a real match still to be played".

    Two conditions, both of which were previously missing and both of which
    leaked rows onto the Predictions page:

    1. **kickoff is today or later.** ``status`` alone is not enough. The feed
       marks a match ``scheduled`` and only flips it to ``finished`` when a later
       sync sees the result, so a kickoff that has passed can still be sitting in
       the table as ``scheduled`` -- hundreds of them, whenever the feed lags. A
       date filter is the only thing that actually means "upcoming".

    2. **source is not the simulated set.** The seeded fixtures are generated
       scorelines, not football. They exist so the model has something to fit
       against offline, and the model prefers real rows when both are present --
       but "prefer" is not "exclude", so a generated fixture could still reach a
       page that presents tips as predictions about real matches.

    Put together, so no caller can apply one and forget the other.
    """
    moment = now or datetime.now(timezone.utc)
    today_start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return and_(
        Fixture.status.in_(["scheduled", "live"]),
        Fixture.kickoff >= today_start,
        Fixture.source != "seed",
    )


#: How many competitions a livescore day may show. A full day across every
#: country runs to well over a hundred; the board is a scannable list, not an
#: exhaustive archive.
LIVESCORE_MAX_COMPETITIONS = 60
#: How far forward a date may be requested. The board runs to the end of the
#: current football season (see :func:`season_bounds`), which is the last day a
#: fixture can plausibly exist. Asking past it returns a genuinely empty day, not
#: a quiet one, so the picker is bounded rather than allowed to wander into
#: next season.
DATE_WINDOW_FORWARD_DAYS = 14
#: How far BACK a date may be requested is fixed at the season boundary too: a
#: reader should not be able to page into last season's archive from the
#: livescore board, where every match is settled and nothing can be updated. The
#: archive itself is still served by the Results page, which is what it is for.
#:
#: The window is computed once per request from the season bounds below rather
#: than being a fixed day count, because "two years back from today" and "the
#: start of the season" are different mornings in December and in April.

#: The European season runs roughly August to May. These are the two ends, as
#: (start month, start day) and (end month, end day). The exact FA/UEFA calendar
#: varies by a week or so year to year, and this is a browse window rather than a
#: competition rulebook, so the conventional bounds are used.
SEASON_START = (8, 1)     # 1 August
SEASON_END = (5, 31)      # 31 May

def season_bounds(today: date) -> tuple[date, date]:
    """The first and last day of the football season containing ``today``.

    August through December belongs to the season that ends the FOLLOWING May;
    January through July belongs to the season that started the previous August.
    Both ends are inclusive. Getting this wrong would clip a season in half at
    New Year and make December's fixtures unreachable from January.
    """
    start_month, start_day = SEASON_START
    end_month, end_day = SEASON_END
    if today >= date(today.year, start_month, start_day):
        # August..December: this season ends next calendar year.
        return date(today.year, start_month, start_day), date(today.year + 1, end_month, end_day)
    if today <= date(today.year, end_month, end_day):
        # January..May: this season started last calendar year.
        return date(today.year - 1, start_month, start_day), date(today.year, end_month, end_day)
    # June/July: the off-season. The window opens on the season about to start so
    # the board is not stuck showing an empty summer with no way forward.
    return date(today.year, start_month, start_day), date(today.year + 1, end_month, end_day)

def season_window(now: datetime) -> tuple[date, date]:
    """The dates the board and its date picker will serve, both inclusive.

    The window is the current football season, extended a little past its end so
    a fixture that kicks off on the last night of the season and lands after
    midnight UTC is still addressable.
    """
    earliest, season_end = season_bounds(now.date())
    return earliest, season_end + timedelta(days=DATE_WINDOW_FORWARD_DAYS)


def _day_bounds(value: str, now: datetime) -> tuple[datetime, datetime]:
    """Resolve the ``date`` query value to a start/end pair in UTC.

    The board serves TODAY FORWARD only. A past date is refused rather than
    quietly served: a board of finished matches is not a livescore board, and
    the Results page is where settled football belongs. Serving them from both
    places gave the app two competing answers to "what happened yesterday".
    """
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if value == "today":
        start = today_start
    elif value == "tomorrow":
        start = today_start + timedelta(days=1)
    else:
        try:
            start = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="date must be today, tomorrow or YYYY-MM-DD",
            ) from None
        # Today and forward only, up to the end of the season. The forward edge
        # keeps its few extra days so a late kick-off that lands after midnight
        # in a UTC+13 timezone is still reachable.
        earliest, latest = season_window(now)
        asked = start.date()
        if asked < today_start.date():
            raise HTTPException(
                status_code=400,
                detail=(
                    f"date {value} is in the past; the board serves today "
                    f"({today_start.date().isoformat()}) forward"
                ),
            )
        if not (earliest <= asked <= latest):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"date {value} is outside the supported window "
                    f"({earliest.isoformat()} .. {latest.isoformat()})"
                ),
            )
    return start, start + timedelta(days=1)


def _is_past_date(value: str, now: datetime) -> bool:
    """Whether a ``date`` value names a day before today.

    Used to *avoid* asking the question of :func:`_day_bounds`, which raises for
    a past day -- some callers (the date picker's own bounds, a legacy deep link)
    need to know the answer rather than be told off for asking.
    """
    today = now.replace(hour=0, minute=0, second=0, microsecond=0).date()
    if value in ("today", "tomorrow"):
        return False
    try:
        return datetime.strptime(value, "%Y-%m-%d").date() < today
    except ValueError:
        return False


# The display label of one competition, or None if the id is unknown.
#
# Assembled from the country and the name to match Competition.label,
# which is the same pairing. Read as two columns rather than concatenated
# in SQL, so the Python property and this query cannot disagree about the
# separator or about what happens when the country is missing.
def _competition_label(db: Session, competition_id: int) -> str | None:
    row = db.execute(
        select(Competition.name, Country.name)
        .select_from(Competition)
        .outerjoin(Country, Competition.country_id == Country.id)
        .where(Competition.id == competition_id)
    ).first()
    if row is None:
        return None
    name, country = row
    return f"{country} - {name}" if country else name


# Every competition id that shares a label with `competition_id`.
#
# Returns None when there is no filter at all, so a caller can branch on it
# directly. A single id with no siblings returns just itself, keeping the
# common case as cheap as the id-equality test it replaces.
#
# The feed keys a competition on its own id, so ONE league is stored more
# than once under different ids: England Premier League exists as both id
# 197 and id 657. The sidebar merges those into a single row and points it
# at whichever id carries the most fixtures, so filtering on that id alone
# silently hides the league's matches stored under its sibling. Verified
# against this database: id 197 returned 3 Premier League results, id 657
# returned 1, and the true total was 4. Resolving to the label makes a
# league filter mean the league.
def _competition_id_set(db: Session, competition_id: int | None) -> list[int] | None:
    if not competition_id:
        return None
    label = _competition_label(db, competition_id)
    if label is None:
        # An id the database does not know cannot match anything. An empty
        # list makes the caller return no rows rather than ignoring the
        # filter and showing every competition -- the worse failure.
        return []
    rows = db.execute(
        select(Competition.id, Competition.name, Country.name)
        .select_from(Competition)
        .outerjoin(Country, Competition.country_id == Country.id)
    ).all()
    ids = [
        cid
        for cid, name, country in rows
        if (f"{country} - {name}" if country else name) == label
    ]
    return ids or [competition_id]

def _livescore_row(fx: Fixture, now: datetime | None = None) -> dict:
    """One synced fixture as a livescore row, with no prediction attached."""
    # The shared clock decides both the minute and whether the match is really
    # still in play. A feed row flagged 'live' hours after kick-off is never
    # closed by the feed, so it is reported finished rather than shown at a
    # minute no football match reaches.
    clock = flashscore.live_clock(fx.kickoff, now) if fx.kickoff else {}
    stale = bool(clock.get("stale"))
    status = "finished" if (fx.status == "live" and stale) else fx.status
    live_minute = (
        flashscore.live_minute_label(fx.kickoff, now)
        if status == "live" and fx.kickoff
        else None
    )
    return {
        "id": str(fx.id),
        "match_id": fx.external_id,
        "competition": fx.competition.name,
        "competition_label": fx.competition.label,
        "country": fx.competition.country.name if fx.competition.country else None,
        "home_team": fx.home_team.name,
        "away_team": fx.away_team.name,
        "home_logo": fx.home_team.logo,
        "away_logo": fx.away_team.logo,
        "home_goals": fx.home_goals,
        "away_goals": fx.away_goals,
        "kickoff": fx.kickoff,
        # The calendar date and the kick-off clock-time of the match, sent as
        # separate scalar strings so the board can print "2026-09-19" and
        # "19:45" without the client having to parse a timestamp to get either.
        # The locale-free ISO/HH:MM form is deliberate: the client formats these
        # into the reader's own timezone, so nothing here is pre-formatted.
        "date": fx.kickoff.date().isoformat() if fx.kickoff else None,
        "time": fx.kickoff.strftime("%H:%M") if fx.kickoff else None,
        "status": status,
        # The minute the match is in. The feed publishes no live clock, so this
        # is derived from kick-off; `minute_source` says so, and the board marks
        # it with a "~" so a reader can tell it apart from the feed's own score.
        # A live match past 90' reads "90+'".
        "minute_label": live_minute if status == "live" and live_minute else "Live" if status == "live" else None,
        "minute_source": "estimated" if status == "live" and live_minute else None,
        # The kick-off clock-time again, kept under an explicit name because the
        # live row needs both facts at once: where the match is in play (the
        # minute) and when it started (this). "Exact" is true of the kick-off
        # time -- the feed publishes it to the second -- and false of nothing
        # else on the row.
        "kickoff_time_exact": fx.kickoff.strftime("%H:%M") if fx.kickoff else None,
        "sportybet_url": _sportybet_result_url(fx),
        # The deep link into THIS app's match page, and the deep link to the
        # source page. Two different destinations and both are wanted: the
        # first keeps the reader in the app, the second is how they check a
        # number against the site it came from.
        "match_url": f"#/match/{fx.external_id}" if fx.external_id else None,
        "flashscore_url": _flashscore_match_url(fx.external_id),
        # Whether this row is one of the eight senior top leagues. Computed here
        # rather than in the client so the top-league definition has ONE home:
        # the same rule decides which fixtures the per-day cap protects and
        # which rows the board's "Top leagues" filter shows, and two copies of
        # it would drift apart.
        "is_top": flashscore.is_top_league(
            fx.competition.country.name if fx.competition.country else None,
            fx.competition.name,
        ),
    }


def _feed_row(m: object) -> dict:
    """One feed match as a livescore row, with no database row behind it."""
    status = getattr(m, "status", "scheduled")
    # Same stale rule as the database-backed row, from the same shared clock:
    # the feed's own 'live' flag is not enough to call a match in play.
    kickoff = getattr(m, "kickoff", None)
    clock = flashscore.live_clock(kickoff) if kickoff else {}
    if status == "live" and clock.get("stale"):
        status = "finished"
    # Rendered from the shared clock so a feed-only row prints the same minute
    # the database-backed one does, rather than the label stored at fetch time.
    minute = flashscore.live_minute_label(kickoff) if status == "live" else None
    return {
        # No fixture id: this match was never written to the database, so the
        # row must not offer a link into a fixture detail page that cannot exist.
        "id": None,
        "match_id": getattr(m, "match_id", None),
        "competition": getattr(m, "competition", "Unknown competition"),
        "competition_label": (
            f"{m.country} - {m.competition}" if getattr(m, "country", None) else getattr(m, "competition", "")
        ),
        "country": getattr(m, "country", None),
        "home_team": getattr(m, "home_team", ""),
        "away_team": getattr(m, "away_team", ""),
        "home_logo": getattr(m, "home_logo", None),
        "away_logo": getattr(m, "away_logo", None),
        "home_goals": getattr(m, "home_goals", None),
        "away_goals": getattr(m, "away_goals", None),
        "kickoff": getattr(m, "kickoff", None),
        "date": getattr(m, "kickoff", None).date().isoformat() if getattr(m, "kickoff", None) else None,
        "time": getattr(m, "kickoff", None).strftime("%H:%M") if getattr(m, "kickoff", None) else None,
        "status": status,
        # Same rule as the database-backed row: the minute is derived, and is
        # labelled as such rather than passed off as a feed value.
        "minute_label": minute if status == "live" and minute else "Live" if status == "live" else None,
        "minute_source": "estimated" if status == "live" and minute else None,
        # `minute` above came from the feed's own derived label at *fetch* time,
        # so it is already current for this response -- no re-derivation needed.
        "kickoff_time_exact": (
            getattr(m, "kickoff", None).strftime("%H:%M")
            if getattr(m, "kickoff", None)
            else None
        ),
        "sportybet_url": _sportybet_live_url(),
        "match_url": (
            f"#/match/{m.match_id}" if getattr(m, "match_id", None) else None
        ),
        "flashscore_url": _flashscore_match_url(getattr(m, "match_id", None)),
        "is_top": flashscore.is_top_league(
            getattr(m, "country", None), getattr(m, "competition", None)
        ),
    }


def _group_by_competition(rows: list[dict]) -> list[dict]:
    """Bucket rows by competition, then order the board for reading.

    Competition order is the Flashscore convention: any competition with a match
    in play floats to the top, then by country and name. Rows inside a group
    stay in kick-off order, with live matches first -- a live score is the only
    thing on the page that is changing, so it is the only thing worth putting
    where the eye lands first.

    Top leagues are lifted above that ordering, because the reading order is
    what was hiding them: a Premier League match played to a finish has no live
    match, so it sorted below every minor league still in play and then fell off
    the group cap entirely. A group counts as top when it holds any top-league
    fixture.
    """
    buckets: dict[str, dict] = {}
    for row in rows:
        key = row["competition_label"] or "Other"
        bucket = buckets.setdefault(
            key,
            {
                "competition": row["competition"] or key,
                "competition_label": key,
                "country": row["country"],
                "items": [],
                "is_top": False,
            },
        )
        bucket["items"].append(row)
        if row.get("is_top"):
            bucket["is_top"] = True

    groups = list(buckets.values())
    for g in groups:
        g["live_count"] = sum(1 for i in g["items"] if i["status"] == "live")
        g["items"].sort(key=lambda r: (r["status"] != "live", r["kickoff"] or datetime.min.replace(tzinfo=timezone.utc)))

    groups.sort(
        key=lambda g: (
            not g["is_top"],
            -g["live_count"],
            (g["country"] or ""),
            g["competition"] or "",
        )
    )
    # The group cap must never drop a top league, for the same reason the match
    # cap must not: the marquee competition is the part of the board a reader is
    # most likely to have come for, and a busy day elsewhere is not a reason to
    # remove it. Top groups are counted first; the rest fill what is left.
    top_groups = [g for g in groups if g["is_top"]]
    rest_groups = [g for g in groups if not g["is_top"]]
    kept = top_groups[:LIVESCORE_MAX_COMPETITIONS]
    if len(kept) < LIVESCORE_MAX_COMPETITIONS:
        kept.extend(rest_groups[: LIVESCORE_MAX_COMPETITIONS - len(kept)])
    return kept


def _competition_out(comp: Competition) -> dict:
    return {
        "id": comp.id,
        "name": comp.name,
        "slug": comp.slug,
        "label": comp.label,
        # The country the competition belongs to, so the sidebar and every
        # competition dropdown can be ordered A-Z by country and then by league.
        # Without it the client only had the "Country - Name" label string and
        # sorted on the league name alone, which interleaved the world's leagues
        # alphabetically instead of grouping each country's together.
        "country": comp.country.name if comp.country else None,
        "is_cup": comp.is_cup,
        "avg_goals": comp.avg_goals,
    }


def _fixture_out(fx: Fixture, prediction: dict | None = None) -> dict:
    return {
        "id": fx.id,
        "kickoff": fx.kickoff,
        "status": fx.status,
        "minute_label": fx.minute_label,
        "season": fx.season,
        "matchday": fx.matchday,
        "venue": fx.venue,
        "competition": _competition_out(fx.competition),
        "home_team": {
            "id": fx.home_team.id,
            "name": fx.home_team.name,
            "short_name": fx.home_team.short_name,
            "logo": fx.home_team.logo,
        },
        "away_team": {
            "id": fx.away_team.id,
            "name": fx.away_team.name,
            "short_name": fx.away_team.short_name,
            "logo": fx.away_team.logo,
        },
        "home_goals": fx.home_goals,
        "away_goals": fx.away_goals,
        "bookmaker": fx.bookmaker,
        "prediction": prediction,
    }


def _client_ip(request: Request) -> str | None:
    """Best-effort client IP, X-Forwarded-For aware."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return request.client.host[:64] if request.client else None

def _admin_user_out(db: Session, user: User) -> dict:
    favs = db.scalar(
        select(func.count()).select_from(Favourite).where(Favourite.user_id == user.id)
    ) or 0
    tips = db.scalar(
        select(func.count()).select_from(TrackedTip).where(TrackedTip.user_id == user.id)
    ) or 0
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "is_premium": bool(user.is_premium),
        "is_admin": bool(user.is_admin),
        "status": user.status,
        "status_reason": user.status_reason,
        "status_changed_at": user.status_changed_at,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
        "last_seen_at": user.last_seen_at,
        "login_count": user.login_count or 0,
        "last_ip": user.last_ip,
        "signup_ip": user.signup_ip,
        "is_online": user.is_online,
        "favourites": favs,
        "tracked_tips": tips,
    }


def _get_fixture_or_404(db: Session, fixture_id: int) -> Fixture:
    fx = db.scalars(_fixture_query().where(Fixture.id == fixture_id)).first()
    if fx is None:
        raise HTTPException(status_code=404, detail="Fixture not found")
    return fx


def _get_or_cache_prediction(
    db: Session, fx: Fixture, refresh: bool = False, commit: bool = True
) -> dict:
    """Predictions for finished/scheduled matches are stable, so cache them.

    `commit=False` lets a caller computing predictions for a whole list commit
    once at the end instead of once per fixture. Each commit is a durability
    write, and a page of fixtures was paying that cost six hundred times over --
    the model work itself was a fraction of the total. The caller that passes
    False owns the commit.
    """
    if not refresh and fx.prediction_json:
        try:
            import json

            cached = json.loads(fx.prediction_json)
            # regenerate if the model version changed
            from .prediction import MODEL_VERSION

            if cached.get("model_version") == MODEL_VERSION:
                return cached
        except Exception:
            pass

    prediction = predict_fixture(db, fx)
    import json

    # store without the datetime objects so JSON is lossless
    serialisable = {**prediction, "generated_at": prediction["generated_at"].isoformat()}
    fx.prediction_json = json.dumps(serialisable, default=str)
    fx.prediction_updated_at = datetime.now(timezone.utc)
    if commit:
        db.commit()
    return prediction


# --------------------------------------------------------------------------
# meta
# --------------------------------------------------------------------------

@router.get("/health", tags=["meta"])
def health(db: Session = Depends(get_db)):
    fixtures = db.scalar(select(func.count()).select_from(Fixture)) or 0
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.version,
        "fixtures": fixtures,
        "ai_provider": "openai" if settings.openai_api_key else "goal-edge-rules",
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/source/status", tags=["meta"])
def source_status(db: Session = Depends(get_db)):
    """How much of the fixture table is real (Flashscore) vs simulated.

    Surfaced so the UI can label real data honestly instead of implying the
    whole app is live.
    """
    counts = fixture_sync.source_counts(db)
    from .refresh import refresher
    return {
        **counts,
        "flashscore_enabled": settings.flashscore_enabled,
        "source_url": settings.flashscore_base_url,
        "refresher": refresher.status(),
    }

@router.post("/admin/refresh/run", tags=["admin"])
async def run_refresh_now(admin: User = Depends(get_current_admin)):
    """Run the daily fixture refresh immediately, in the background.

    The timer does this on its own; this exists so an operator can force a
    pull (and see the outcome) without waiting for the next tick.
    """
    from .refresh import refresher
    return await refresher.run_once()

@router.post("/admin/sync", tags=["admin"])
def run_sync(
    date: str = Query("today", description="today | tomorrow | yesterday | YYYY-MM-DD"),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Pull real fixtures/results for one day from the Flashscore feed.

    Safe to call repeatedly: matches are upserted by their upstream id, so a
    re-run during the day updates statuses and scores in place.
    """
    result = fixture_sync.sync_day(db, date)
    if result.error:
        raise HTTPException(status_code=503, detail=result.error)
    _audit(db, admin, "sync", date, f"fetched={result.fetched} updated={result.updated}")
    return result.as_dict()

@router.get("/fixtures/real", tags=["fixtures"])
def real_fixtures(
    date: str = Query("today", description="today | tomorrow | yesterday | YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """Real fixtures for a day, straight from the live feed (no DB write).

    Use this when the freshest possible schedule is wanted and a database sync
    has not been run yet.
    """
    from . import flashscore as fs
    try:
        matches = fs.fetch_matches_for_day(date)
    except fs.FlashscoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return {
        "date": date,
        "total": len(matches),
        "source": settings.flashscore_base_url,
        "items": [
            {
                "match_id": m.match_id,
                "competition": m.competition,
                "country": m.country,
                "home_team": m.home_team,
                "away_team": m.away_team,
                "home_logo": m.home_logo,
                "away_logo": m.away_logo,
                "kickoff": m.kickoff,
                "status": m.status,
                "home_goals": m.home_goals,
                "away_goals": m.away_goals,
            }
            for m in matches
        ],
    }

@router.get("/livescores", tags=["fixtures"])
def livescores(
    date: str = Query("today", description="today | tomorrow | yesterday | YYYY-MM-DD"),
    live_only: bool = Query(False, description="Only matches currently in play"),
    top_only: bool = Query(
        False,
        description=(
            "Only the eight major European leagues (Premier League, LaLiga, Serie A, "
            "Bundesliga, Ligue 1, Eredivisie, Champions League, Europa League)"
        ),
    ),
    alert: bool = False,
    sync: bool = Query(
        True,
        description=(
            "When the day has not been synced yet, ingest it so every row has a "
            "match-details page. Set false to serve the feed read-only."
        ),
    ),
    db: Session = Depends(get_db),
):
    """A Flashscore-style livescore board for one day, grouped by competition.

    Deliberately NOT the same payload as ``/fixtures``: a livescore page needs
    three columns (status, score, kick-off) and nothing else, so this skips
    prediction generation entirely -- building a Dixon-Coles matrix for every
    fixture on the planet would make the page unusably slow, and the numbers it
    would produce have no place beside a live score anyway.

    Two sources, in order of preference:

    1. **The database** -- fixtures already synced from the feed. Chosen when it
       holds the day being asked for, because it works without a network call
       and keeps the page responsive.
    2. **The live feed** -- fetched directly when the database has nothing for
       that day (any day in the supported two-year window, not just the last
       three).

    A day that arrives from the second source is **ingested** before being
    returned (``sync=true``, the default) so each row carries a real fixture id
    and its match-details page exists. Without that ingest a feed-only row has
    no id and cannot be opened, so the details the board promises would be
    unreachable on exactly the days that are not yet synced. The ingest is the
    same upsert the scheduled refresher runs, keyed on the feed's own match id,
    so re-serving a day cannot duplicate a row; set ``sync=false`` to keep the
    request strictly read-only.

    Every row is therefore real football or nothing: a match is never invented
    to fill a competition group.
    """
    now = datetime.now(timezone.utc)
    day_start, day_end = _day_bounds(date, now)

    stmt = (
        _fixture_query()
        .where(Fixture.kickoff >= day_start, Fixture.kickoff < day_end)
        .order_by(Fixture.kickoff)
    )
    fixtures = db.scalars(stmt).all()

    rows: list[dict] = []
    source = "database"
    if fixtures:
        rows = [_livescore_row(fx, now) for fx in fixtures]
    else:
        from . import flashscore as fs
        try:
            matches = fs.fetch_matches_for_day(date)
        except (fs.FlashscoreUnavailable, ValueError) as exc:
            # An unreachable feed is not a server error here: the day is simply
            # empty, and the page says so rather than failing to load.
            return {
                "date": date,
                "day": day_start.date().isoformat(),
                "source": "unavailable",
                "error": str(exc),
                "live_count": 0,
                "total": 0,
                "groups": [],
            }

        # Ingest, then read back what the database now holds. Doing it in that
        # order means the board shows the same rows a second visit would get
        # straight from the database -- with ids, and therefore clickable --
        # rather than a parallel feed-shaped copy that differs from the stored
        # one. A failed ingest is not fatal: the feed rows are still shown, just
        # without links, which is the pre-existing behaviour.
        source = "live-feed"
        if sync and matches:
            try:
                fixture_sync.sync_day(db, date)
            except Exception:  # noqa: BLE001 - the board must render regardless
                pass
            stored = db.scalars(
                _fixture_query()
                .where(Fixture.kickoff >= day_start, Fixture.kickoff < day_end)
                .order_by(Fixture.kickoff)
            ).all()
            if stored:
                rows = [_livescore_row(fx, now) for fx in stored]
                source = "live-feed-synced"
        if not rows:
            rows = [_feed_row(m) for m in matches]

    # The top-league filter is applied FIRST, then live-only, because a reader
    # who narrows to the top leagues still wants the whole day's card there --
    # not just the part of it already in play.
    if top_only:
        rows = [r for r in rows if r.get("is_top")]
    live = [r for r in rows if r["status"] == "live"]
    top_count = sum(1 for r in rows if r.get("is_top"))
    shown = live if live_only else rows
    # Match cap: a busy day carries well over a thousand fixtures, and only so
    # many can be grouped and rendered usefully. Applied to the flat list before
    # grouping, which is where the top-league protection lives.
    capped = shown[: settings.flashscore_max_matches_per_day]
    # Grouping cuts again, to the competition cap. That second cut is why the
    # reported live count must be taken AFTER it: counting from `capped` said
    # "25 in play" beside a board that showed none, because the 60-group limit
    # had already dropped the rows holding them. The number a reader sees has to
    # describe the rows a reader sees.
    groups = _group_by_competition(capped)
    visible = [r for g in groups for r in g["items"]]
    live_count = sum(1 for r in visible if r["status"] == "live")
    season_start, season_end = season_bounds(now.date())
    latest_day = season_window(now)[1]
    return {
        "date": date,
        # `day` is the resolved calendar day (so "today" comes back as a date),
        # and the window pair lets the date picker bound itself to exactly what
        # the server will serve rather than guessing at a range in the client.
        "day": day_start.date().isoformat(),
        "window": {
            # The board opens on today and cannot go back: `earliest` is today,
            # not the start of the season. A picker that offered last November
            # was offering a board of settled matches the Results page owns.
            "earliest": now.date().isoformat(),
            "latest": latest_day.isoformat(),
            # Kept for compatibility with any caller that still reads a day count.
            # It is now the distance to the season boundary, not a fixed 730.
            "back_days": 0,
            "forward_days": (latest_day - now.date()).days,
            # The season itself, so the UI can say "2025/26 season" rather than
            # having to infer it from two bare dates.
            "season": f"{season_start.year}/{str(season_end.year)[2:]}",
        },
        "source": source,
        "generated_at": now.isoformat(),
        "live_count": live_count,
        # How many of the rows on this response are top-league fixtures. The board
        # uses it to label the "Top leagues" filter with a real count rather than
        # a number it guessed, so an empty top-league day reads as "Top leagues
        # (0)" instead of a filter that looks like it is broken.
        "top_count": top_count,
        # The rows a reader actually gets, which is what "showing first N" in the
        # toolbar has to mean. Reporting the pre-grouping count made that caption
        # promise rows the group cap had already removed.
        "total": len(visible),
        "truncated": len(shown) > len(visible),
        "groups": groups,
    }


# ---------------------------------------------------------------------------
# Match page
# ---------------------------------------------------------------------------
#
# Clicking a row on the livescore board opens a page for that *match* rather
# than for the model's fixture. The two are different documents and the
# difference matters:
#
#   * `/fixtures/{id}` is the model's view -- a Dixon-Coles matrix, priced
#     markets and an AI preview. It is about a match that has mostly not
#     happened yet.
#   * `/matches/{mid}` is the match's own view -- the score, the minute, the
#     events, the statistics. It works for a fixture the model has no opinion
#     about (a match already in play, a match in a league the app does not
#     model), because it needs nothing from the engine.
#
# The match page therefore stands on its own, and links *into* the model page
# only when a stored fixture exists to link to. That is what keeps it usable on
# a live match, which is exactly when the model has nothing to say.


def _flashscore_match_url(match_id: str | None) -> str | None:
    """The public Flashscore URL for a match id, for verification at source."""
    if not match_id:
        return None
    return f"{settings.flashscore_base_url.rstrip('/')}/match/{match_id}/"


def _stored_fixture(db: Session, match_id: str) -> Fixture | None:
    # The stored fixture behind a feed match id, if the app has one.
    return db.scalars(
        _fixture_query().where(Fixture.external_id == match_id)
    ).first()


def _match_page_base(db: Session, match_id: str) -> dict:
    """The local facts about a match: teams, competition, kick-off, score.

    Read from the synced fixture when one exists, so the page has something to
    render before (and even without) the live feed. A match that was never
    synced still gets a page -- built from the day feed -- because the reader
    clicked it on a board that was showing it.
    """
    fx = _stored_fixture(db, match_id)
    if fx is not None:
        return {
            "fixture_id": str(fx.id),
            "match_id": match_id,
            "kickoff": fx.kickoff,
            "status": fx.status,
            "competition": {
                "name": fx.competition.name,
                "label": fx.competition.label,
                "country": fx.competition.country.name if fx.competition.country else None,
            },
            "home_team": {"name": fx.home_team.name, "logo": fx.home_team.logo},
            "away_team": {"name": fx.away_team.name, "logo": fx.away_team.logo},
            "home_goals": fx.home_goals,
            "away_goals": fx.away_goals,
            "source": "database",
        }

    # Not stored: read the context from the match's own feed. Scanning the day
    # feeds for the id is NOT enough -- the day feed is capped at a few hundred
    # matches, so a match in a league outside that cap is absent from it even
    # though the reader clicked it on a board that showed it. The match's own
    # history feed opens with the match as its first record, which names both
    # teams, the competition, the country, the kick-off and the score.
    context = matchdetail.fetch_match_context(match_id)
    if context is not None:
        return {
            "fixture_id": None,
            "match_id": match_id,
            "kickoff": context.get("kickoff"),
            # A match with a context record may be upcoming, in play, or over.
            # Guessing "scheduled" would show a kick-off time where a score and
            # a minute belong, so the status is resolved from the feeds' own
            # signals -- the published period, the status code, and the
            # kick-off time against the clock.
            "status": matchdetail.fetch_status(match_id, context.get("kickoff")),
            "competition": context.get("competition"),
            "home_team": context.get("home_team"),
            "away_team": context.get("away_team"),
            "home_goals": context.get("home_goals"),
            "away_goals": context.get("away_goals"),
            "source": "feed",
        }
    return {
        "fixture_id": None,
        "match_id": match_id,
        "kickoff": None,
        "status": "scheduled",
        "competition": None,
        "home_team": None,
        "away_team": None,
        "home_goals": None,
        "away_goals": None,
        "source": "unknown",
    }


@router.get("/matches/{match_id}", tags=["matches"])
def get_match(match_id: str, db: Session = Depends(get_db)):
    """The full match page for one match id, from the live feeds.

    Returns the local facts *and* the match's own live data -- the minute, the
    score, the events and the statistics -- so one request paints the whole
    page. The two halves are reported separately and neither is invented:
    ``available: false`` on the live half means the clock is derived from
    kick-off and is labelled as such, not that the page is empty.

    Never 503s on a feed outage. The reader clicked a match that was on screen;
    failing to open its page because an upstream blinked is the wrong trade.
    """
    base = _match_page_base(db, match_id)

    # A match the app has never stored gets stored now, so the "Full analysis &
    # prediction" button above it always leads somewhere. This is the difference
    # between a page that can only *describe* a match and one that can hand it
    # to the model: the engine needs a Fixture row, and the reader should not
    # have to care whether today's synced slice happened to include it.
    #
    # Best-effort by design. A failed ingest leaves `fixture_id` null and the
    # page simply says there is no prediction available -- it must never turn a
    # readable match page into an error page.
    if base.get("fixture_id") is None and base.get("source") == "feed":
        try:
            stored = fixture_sync.sync_one_match(db, match_id)
            if stored is not None:
                base = _match_page_base(db, match_id)
        except Exception:  # noqa: BLE001 - the page renders regardless
            pass
    live = matchdetail.build_match_detail(
        match_id,
        kickoff=base.get("kickoff"),
        status=base.get("status") or "scheduled",
        home_goals=base.get("home_goals"),
        away_goals=base.get("away_goals"),
    )

    # The feed's score wins once it has one: between syncs it is the fresher
    # of the two, and the whole point of a match page is to be current.
    for side in ("home", "away"):
        if live["score"].get(side) is not None:
            base[f"{side}_goals"] = live["score"][side]

    # A match the feed still flags 'live' long past full time is over -- the row
    # was simply never closed. Same rule the livescore board applies, from the
    # same shared clock, so the two surfaces cannot disagree: the page says
    # Finished where the board does, instead of showing "90+11'" on one and
    # "Finished" on the other.
    if base.get("status") == "live" and live["clock"].get("stale"):
        base["status"] = "finished"

    return {
        **base,
        "flashscore_url": _flashscore_match_url(match_id),
        "live": {
            "available": live["available"],
            "clock": live["clock"],
            "clock_label": live["clock_label"],
            "period": live["period"],
            "score_source": live["score"]["source"],
            "generated_at": live["fetched_at"],
        },
        "events": live["events"],
        "stats": live["stats"],
        "stats_available": live["stats_available"],
        # The lineups and the match-information block (referee, venue, city,
        # attendance). Both are what the reference page lists beside its own
        # timeline, and both come from the match's own feeds rather than from
        # anything the model computes.
        "lineups": live["lineups"],
        "info": live["info"],
    }


@router.get("/matches/{match_id}/live", tags=["matches"])
def get_match_live(match_id: str, db: Session = Depends(get_db)):
    """Just the live clock and score, for the match page's minute-by-minute poll.

    Deliberately small: the page polls this while a match is in play, and
    re-fetching the whole statistics payload every 30 seconds would be a
    wasteful way to move a number by one.
    """
    base = _match_page_base(db, match_id)
    live = matchdetail.build_match_detail(
        match_id,
        kickoff=base.get("kickoff"),
        status=base.get("status") or "scheduled",
        home_goals=base.get("home_goals"),
        away_goals=base.get("away_goals"),
    )
    # Same stale rule as the full page and the livescore board: a row the feed
    # left 'live' long past full time is over, so the poll reports it finished
    # instead of handing the page a status that contradicts its own clock.
    status = base.get("status")
    if status == "live" and live["clock"].get("stale"):
        status = "finished"
    return {
        "match_id": match_id,
        "status": status,
        "available": live["available"],
        "clock": live["clock"],
        "clock_label": live["clock_label"],
        "period": live["period"],
        "home_goals": live["score"]["home"],
        "away_goals": live["score"]["away"],
        "score_source": live["score"]["source"],
        "event_count": len(live["events"]),
        "latest_event": live["events"][-1] if live["events"] else None,
        "generated_at": live["fetched_at"],
    }

@router.get("/countries", response_model=list[CountryOut], tags=["meta"])
def list_countries(db: Session = Depends(get_db)):
    return db.scalars(select(Country).order_by(Country.name)).all()

@router.get("/days", tags=["meta"])
def day_summary(db: Session = Depends(get_db)):
    """Per-competition fixture counts for today and tomorrow, plus the totals.

    The sidebar's All/Today/Tomorrow tabs have to say how many fixtures a day
    holds before narrowing to it, and they have to know WHICH competitions play
    that day so a league that does not cannot be offered. Deriving that from the
    tips feed would be wrong twice over: the feed is capped, and it only contains
    fixtures the model has a pick for.

    This counts straight from the fixtures table, so it is a real total and it
    costs one grouped query rather than a prediction per row.
    """
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    bounds = {
        "today": today_start,
        "tomorrow": today_start + timedelta(days=1),
    }

    out: dict = {"days": {}, "generated_at": now.isoformat()}
    for name, start in bounds.items():
        end = start + timedelta(days=1)
        rows = db.execute(
            select(Competition.id, Competition.name, Competition.slug, func.count(Fixture.id))
            .join(Fixture, Fixture.competition_id == Competition.id)
            .where(
                Fixture.kickoff >= start,
                Fixture.kickoff < end,
                # Same source rule as the list these badges sit beside: a count
                # that included simulated fixtures would promise rows the
                # Predictions page refuses to show.
                Fixture.source != "seed",
            )
            .group_by(Competition.id)
            .order_by(func.count(Fixture.id).desc())
        ).all()
        out["days"][name] = {
            "date": start.date().isoformat(),
            "total": sum(r[3] for r in rows),
            "competitions": [
                {"id": r[0], "name": r[1], "slug": r[2], "count": r[3]} for r in rows
            ],
        }

    # The "all upcoming" total, for the same reason: the sidebar shows it, and it
    # must be the real count rather than however many the feed happened to return.
    # Counted with the shared filter, so this badge agrees with the list it sits
    # beside instead of including past-dated rows and simulated fixtures.
    upcoming = db.scalar(
        select(func.count()).select_from(Fixture).where(_upcoming_filter(now))
    )
    out["upcoming_total"] = upcoming or 0
    return out


@router.get("/competitions", response_model=list[CompetitionOut], tags=["meta"])
def list_competitions(
    country: str | None = None,
    db: Session = Depends(get_db),
):
    stmt = select(Competition).options(selectinload(Competition.country))
    if country:
        stmt = stmt.join(Country).where(Country.name.ilike(f"%{country}%"))
    comps = db.scalars(stmt.order_by(Competition.tier, Competition.name)).all()
    return [_competition_out(c) for c in comps]


@router.get("/teams", response_model=list[TeamOut], tags=["meta"])
def list_teams(
    q: str | None = None,
    competition_id: int | None = None,
    limit: int = Query(200, le=2000),
    db: Session = Depends(get_db),
):
    stmt = select(Team)
    if q:
        stmt = stmt.where(Team.name.ilike(f"%{q}%"))
    if competition_id:
        stmt = stmt.where(Team.competition_id == competition_id)
    return db.scalars(stmt.order_by(Team.name).limit(limit)).all()


# --------------------------------------------------------------------------
# fixtures + predictions
# --------------------------------------------------------------------------

@router.get("/fixtures", response_model=FixtureListResponse, tags=["fixtures"])
def list_fixtures(
    db: Session = Depends(get_db),
    date: str | None = Query(None, description="today | tomorrow | YYYY-MM-DD"),
    competition_id: int | None = None,
    country: str | None = None,
    status: str | None = Query(None, pattern="^(scheduled|live|finished)$"),
    min_confidence: int | None = Query(None, ge=0, le=100),
    only_value: bool = False,
    search: str | None = None,
    with_prediction: bool = True,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort: str = Query("kickoff", pattern="^(kickoff|confidence|edge|probability)$"),
):
    stmt = _fixture_query()

    now = datetime.now(timezone.utc)
    if date == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = stmt.where(Fixture.kickoff >= start, Fixture.kickoff < start + timedelta(days=1))
        stmt = stmt.where(Fixture.status.in_(["scheduled", "live"]))
    elif date == "tomorrow":
        start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = stmt.where(Fixture.kickoff >= start, Fixture.kickoff < start + timedelta(days=1))
    elif date == "yesterday":
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = stmt.where(Fixture.kickoff >= start, Fixture.kickoff < start + timedelta(days=1))
    elif date:
        try:
            d = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be today, tomorrow, yesterday or YYYY-MM-DD") from None
        stmt = stmt.where(Fixture.kickoff >= d, Fixture.kickoff < d + timedelta(days=1))
    elif status is None:
        # Default view: what's still to be played. _upcoming_filter, not just the
        # status check -- a past-dated row can still read "scheduled", and the
        # simulated fixtures have no place in a fixture listing.
        stmt = stmt.where(_upcoming_filter(now))

    if competition_id:
        stmt = stmt.where(Fixture.competition_id == competition_id)
    if country:
        stmt = stmt.join(Competition, Fixture.competition_id == Competition.id).join(
            Country, Competition.country_id == Country.id
        ).where(Country.name.ilike(f"%{country}%"))
    if status:
        stmt = stmt.where(Fixture.status == status)
    if search:
        stmt = stmt.join(Team, or_(Team.id == Fixture.home_team_id, Team.id == Fixture.away_team_id)).where(
            Team.name.ilike(f"%{search}%")
        )

    fixtures = db.scalars(stmt.order_by(Fixture.kickoff)).all()

    items: list[dict] = []
    # Same batched-commit rule as /tips: predictions computed here are committed
    # once, after the loop, rather than once per fixture.
    for fx in fixtures:
        prediction = (
            _get_or_cache_prediction(db, fx, commit=False) if with_prediction else None
        )
        if prediction:
            if min_confidence is not None and prediction["confidence"] < min_confidence:
                continue
            if only_value and not any(
                s["value"] for s in flat_selections(prediction)
            ):
                continue
        items.append(_fixture_out(fx, prediction))
    if with_prediction:
        db.commit()

    if sort == "confidence":
        items.sort(key=lambda i: i["prediction"]["confidence"] if i["prediction"] else 0, reverse=True)
    elif sort == "edge":
        # best_edge is None on a self-priced fixture (no market to disagree with),
        # so it cannot be compared numerically; sort it as "no edge".
        items.sort(
            key=lambda i: (i["prediction"]["best_edge"] or 0.0) if i["prediction"] else 0.0,
            reverse=True,
        )
    elif sort == "probability":
        items.sort(
            key=lambda i: i["prediction"]["best_probability"] if i["prediction"] else 0, reverse=True
        )

    total = len(items)
    pages = max(1, math.ceil(total / page_size))
    start_idx = (page - 1) * page_size
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "items": items[start_idx : start_idx + page_size],
    }


@router.get("/fixtures/{fixture_id}", response_model=FixtureOut, tags=["fixtures"])
def get_fixture(
    fixture_id: int,
    refresh: bool = False,
    db: Session = Depends(get_db),
):
    fx = _get_fixture_or_404(db, fixture_id)
    prediction = _get_or_cache_prediction(db, fx, refresh=refresh)
    return _fixture_out(fx, prediction)


@router.get("/fixtures/{fixture_id}/prediction", tags=["predictions"])
def get_prediction(
    fixture_id: int,
    refresh: bool = False,
    db: Session = Depends(get_db),
):
    fx = _get_fixture_or_404(db, fixture_id)
    return _get_or_cache_prediction(db, fx, refresh=refresh)

@router.get("/fixtures/{fixture_id}/history", tags=["fixtures"])
def get_fixture_history(fixture_id: int, db: Session = Depends(get_db)):
    """Real results history and stats for the two teams in a fixture.

    Powers the match page's Form and Head-to-head tabs. Everything returned is
    counted from actual played matches -- recent form for each side and their
    past meetings -- so no number here is estimated or invented.

    Degrades to ``available: false`` when the upstream feed cannot be reached,
    rather than failing the page: the model's own analysis stays usable.
    """
    from .history import fetch_match_context
    fx = _get_fixture_or_404(db, fixture_id)
    if not fx.external_id:
        return {
            "fixture_id": fixture_id,
            "available": False,
            "reason": "no_external_id",
            "message": (
                "This fixture is simulated demo data, so it has no real match "
                "to look up a history for."
            ),
            "home_form": None,
            "away_form": None,
            "head_to_head": None,
        }

    context = fetch_match_context(
        fx.external_id,
        fx.home_team.name,
        fx.away_team.name,
    )
    context["fixture_id"] = fixture_id
    context["source"] = "flashscore"
    context["match"] = f"{fx.home_team.name} vs {fx.away_team.name}"
    return context
@router.get("/fixtures/{fixture_id}/sportybet-markets", tags=["markets"])
def get_sportybet_markets(
    fixture_id: int,
    groups: str | None = Query(
        None,
        description="Comma-separated market groups to pin to the top, e.g. 'Match Result,Goals'.",
    ),
    max_markets: int = Query(120, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """SportyBet's own market board for this fixture.

    Display only. These prices are never blended into the model: the response
    carries no probability, edge or confidence, and the frontend renders it in
    a panel separate from the model's selections.
    """
    fx = _get_fixture_or_404(db, fixture_id)
    wanted = [g for g in (groups.split(",") if groups else []) if g.strip()] or None
    board = sportybet_feed.fetch_fixture_board(
        fx, groups=wanted, max_markets=max_markets
    )
    board["fixture_id"] = fixture_id
    board["match"] = f"{fx.home_team.name} vs {fx.away_team.name}"
    return board


@router.get("/fixtures/{fixture_id}/ai", response_model=AiAnalysisOut, tags=["ai"])
async def get_ai_analysis(
    fixture_id: int,
    refresh: bool = False,
    db: Session = Depends(get_db),
):
    fx = _get_fixture_or_404(db, fixture_id)
    prediction = _get_or_cache_prediction(db, fx, refresh=refresh)

    meta = {
        "home_team": fx.home_team.display_name,
        "away_team": fx.away_team.display_name,
        "competition": fx.competition.label,
        "kickoff": fx.kickoff.strftime("%d %b %Y %H:%M UTC"),
    }
    result = await ai_layer.generate_analysis(prediction, meta)

    # persist the narrative on the cached prediction so the list view can use it
    summary = ai_layer.build_one_line_summary(prediction, meta)
    prediction["ai_summary"] = summary
    prediction["ai_headline"] = result.headline

    return {
        "fixture_id": fixture_id,
        "provider": result.provider,
        "model": result.model,
        "headline": result.headline,
        "analysis": result.analysis,
        "betting_angle": result.betting_angle,
        "key_factors": result.key_factors or prediction.get("key_factors", []),
        "risk_notes": result.risk_notes or prediction.get("risk_notes", []),
        "cached": False,
    }


@router.get("/tips", tags=["predictions"])
def list_tips(
    db: Session = Depends(get_db),
    sort: str = Query(
        "kickoff",
        pattern="^(kickoff|confidence|edge)$",
        description=("kickoff = chronological (the default); "
                     "confidence and edge order by model strength instead"),
    ),
    date: str | None = None,
    competition_id: int | None = None,
    min_confidence: int = Query(0, ge=0, le=100),
    limit: int = Query(20, ge=1, le=100),
    only_value: bool = False,
    source: str | None = Query(
        None,
        pattern="^(seed|flashscore)$",
        description="Force one source; by default real fixtures are preferred",
    ),
):
    """The front-page tips feed: one headline pick per upcoming fixture.

    Bounded on purpose: the candidate window is capped so the prediction pass
    stays a few seconds rather than minutes once a week of fixtures is synced.

    "Upcoming" is enforced by ``_upcoming_filter``: a kickoff today or later AND
    a real (non-simulated) source. Filtering on ``status`` alone hundreds of
    past-dated rows through, because the feed leaves a match ``scheduled`` until
    a later sync observes its result.

    That filter bounds the day from MIDNIGHT, which is right for a fixture
    listing but not for this page: a tip is a claim about a match you can still
    back, so anything that has already kicked off today has to go. ``kickoff >
    now`` is applied on top for exactly that reason -- otherwise the morning's
    finished matches sat at the top of the list wearing a pick, presented as
    advice for a match that was over.
    """
    now = datetime.now(timezone.utc)
    stmt = _fixture_query().where(_upcoming_filter(now), Fixture.kickoff > now)

    if date == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = stmt.where(Fixture.kickoff >= start, Fixture.kickoff < start + timedelta(days=1))
    elif date == "tomorrow":
        start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = stmt.where(Fixture.kickoff >= start, Fixture.kickoff < start + timedelta(days=1))

    # Narrowing by competition has to happen in the query, not after the `limit`
    # slice: a league whose fixtures all sorted past the cap otherwise came back
    # as an empty list even though the league had football on.
    if competition_id:
        stmt = stmt.where(Fixture.competition_id == competition_id)

    if source:
        stmt = stmt.where(Fixture.source == source)

    # The candidate set has to be bounded BEFORE predictions are computed. A
    # prediction is a full Dixon-Coles fit per fixture, and now that the schedule
    # is synced a week forward there are a couple of thousand upcoming fixtures;
    # scoring every one of them to return sixty was a multi-minute request.
    #
    # The window below is generous relative to what the page shows (``limit`` is
    # capped at 100) and the list is sorted by confidence before the slice, so the
    # picks a reader sees are still drawn from a wide field -- just not from the
    # entire season.
    TIP_CANDIDATE_LIMIT = 600
    fixtures = db.scalars(
        stmt.order_by(Fixture.kickoff).limit(TIP_CANDIDATE_LIMIT)
    ).all()

    # `source=seed` is still honoured above for callers that explicitly want the
    # simulated dataset (a demo, a test), but nothing here falls back to it: if
    # the filter left no real fixtures, the honest answer is an empty list.
    tips = []
    # commit=False: every fixture the model has not priced yet is written to the
    # session and committed ONCE after the loop. Committing per fixture made a
    # six-hundred-fixture page pay six hundred durability writes for a handful of
    # rows that were actually new; the rest were cache hits that changed nothing.
    for fx in fixtures:
        p = _get_or_cache_prediction(db, fx, commit=False)
        if p["confidence"] < min_confidence:
            continue
        if only_value and not any(s["value"] for s in flat_selections(p)):
            continue
        tips.append(

            {
                "fixture_id": fx.id,
                "kickoff": fx.kickoff,
                "competition": fx.competition.label,
                "country": fx.competition.country.name if fx.competition.country else None,
                "home_team": fx.home_team.display_name,
                "away_team": fx.away_team.display_name,
                "home_team_full": fx.home_team.name,
                "away_team_full": fx.away_team.name,
                "home_logo": fx.home_team.logo,
                "away_logo": fx.away_team.logo,
                # Provenance: a real feed match must be distinguishable from a
                # simulated one, in the UI and in the API.
                "source": fx.source,
                "is_real": fx.source == "flashscore",
                "market": p["best_market"],
                "selection": p["best_selection"],
                "probability": p["best_probability"],
                "fair_odds": round(1 / p["best_probability"], 2) if p["best_probability"] else None,
                "odds": p["best_odds"],
                "edge": p["best_edge"],
                "confidence": p["confidence"],
                "confidence_label": p["confidence_label"],
                "value_rating": p["value_rating"],
                # Full market breakdown so the predictions list can show every
                # selection (1X2, O/U lines, BTTS), not just the headline tip.
                "markets": p["markets"],
                "home_win": p["home_win"],
                "draw": p["draw"],
                "away_win": p["away_win"],
                "expected_total_goals": p["expected_total_goals"],
                "ai_summary": ai_layer.build_one_line_summary(p, {
                    "home_team": fx.home_team.display_name,
                    "away_team": fx.away_team.display_name,
                }),
            }
        )

    # One commit for the whole pass, covering every prediction computed above.
    db.commit()

    # Ordering is explicit, because the ORDER BY above only chose WHICH
    # fixtures were considered -- the slice below happens after this sort, so
    # this is the order the reader actually sees.
    #
    # kickoff is the default: the page is a schedule, and a schedule reads in
    # time order. confidence and edge remain available for a caller that wants
    # the model's strongest picks first.
    if sort == "confidence":
        tips.sort(key=lambda t: (t["confidence"], t["edge"] or 0.0), reverse=True)
    elif sort == "edge":
        # edge is None when the fixture has no independent market (self-priced),
        # so it cannot take part in a numeric sort. Treat unknown as "no edge".
        tips.sort(key=lambda t: (t["edge"] or 0.0, t["confidence"]), reverse=True)
    else:
        # Kick-off ascending, with the fixture id as a tiebreaker so two
        # matches at the same minute hold a stable order across requests
        # rather than shuffling on every poll.
        tips.sort(key=lambda t: (t["kickoff"], t["fixture_id"]))
    return {"total": len(tips), "items": tips[:limit]}

# Live-score deep link, so a settled result can be checked on the book itself.
# SportyBet exposes both a livescore and a results board; we point at the
# country the rest of the app already talks to.
def _sportybet_live_url() -> str:
    return f"https://www.sportybet.com/{settings.sportybet_country}/livescore"


def _sportybet_result_url(fx: Fixture) -> str:
    return f"https://www.sportybet.com/{settings.sportybet_country}/results"

@router.get("/results", tags=["predictions"])
def list_results(
    db: Session = Depends(get_db),
    competition_id: int | None = None,
    days: int = Query(1, ge=1, le=3, description="How far back to grade results (1-3 days)"),
    graded_only: bool = Query(
        False, description="Only matches where the model's headline pick was graded"
    ),
    source: str | None = Query(
        None, pattern="^(seed|flashscore)$",
        description="Limit to real (flashscore) or simulated (seed) results",
    ),
    limit: int = Query(200, ge=1, le=2000),
):
    """Settled matches graded against the model's published prediction.

    Each row carries the final score, the model's headline tip, whether that
    tip landed (``hit``), the full per-market breakdown, and a SportyBet
    live-score link so a user can verify the result on the book itself.
    """
    now = datetime.now(timezone.utc)
    # The window is a count of WHOLE DAYS back, counted from the start of today
    # rather than from this instant.
    #
    # The difference matters: "last day" measured from 01:00 would begin at 01:00
    # yesterday, so it would drop last night's 20:00 kick-offs -- exactly the
    # matches the reader opened the page to check. Anchoring to midnight makes
    # "last day" mean today and yesterday, which is what the phrase says.
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    since = today_start - timedelta(days=days - 1)

    # Ascending by kick-off, so the Results page reads as a chronological list:
    # the earliest settled match first and the most recent last. Newest-first is
    # the more common convention for a results feed, but ascending is what was
    # asked for here and it matches the Predictions page's own ordering, so the
    # two tabs scan the same way.
    stmt = (
        _fixture_query()
        .where(Fixture.status == "finished", Fixture.kickoff >= since)
        .order_by(Fixture.kickoff)
    )
    # Resolved to every id sharing the label, so a merged league is filtered
    # whole rather than only under the one id the sidebar happened to pick.
    comp_ids = _competition_id_set(db, competition_id)
    if comp_ids is not None:
        stmt = stmt.where(Fixture.competition_id.in_(comp_ids))
    if source:
        stmt = stmt.where(Fixture.source == source)

    # The cap belongs on the QUERY, not on the finished list. Grading needs a
    # prediction per fixture, so slicing afterwards meant a three-day window
    # graded every settled match in it -- well over a thousand model fits -- only
    # to throw most of them away. Asking the database for `limit` rows means the
    # expensive part is bounded by the same number the response is.
    fixtures = db.scalars(stmt.limit(limit)).all()

    rows: list[dict] = []
    # commit=False, then one commit after the loop. A settled fixture usually has
    # no cached prediction (its kickoff passed before anything asked for one), so
    # this loop computes one per match -- and the default commit-per-fixture made
    # that one durability write each. Over a three-day window that was well over a
    # thousand writes for a page that only needed the rows.
    for fx in fixtures:
        if fx.home_goals is None or fx.away_goals is None:
            continue
        p = _get_or_cache_prediction(db, fx, commit=False)
        graded = grade_prediction(p, fx.home_goals, fx.away_goals)
        hit = graded["headline_hit"]
        if graded_only and hit is None:
            continue
        rows.append(
            {
                "fixture_id": fx.id,
                "kickoff": fx.kickoff,
                "competition": fx.competition.label,
                "home_team": fx.home_team.display_name,
                "away_team": fx.away_team.display_name,
                "home_team_full": fx.home_team.name,
                "away_team_full": fx.away_team.name,
                "home_logo": fx.home_team.logo,
                "away_logo": fx.away_team.logo,
                "home_goals": fx.home_goals,
                "away_goals": fx.away_goals,
                "market": p["best_market"],
                "prediction": p["best_selection"],
                "probability": p["best_probability"],
                "odds": p["best_odds"],
                "confidence": p["confidence"],
                "confidence_label": p["confidence_label"],
                "expected_home_goals": p["expected_home_goals"],
                "expected_away_goals": p["expected_away_goals"],
                # True = tick, False = cross, None = not a market the engine prices.
                "hit": hit,
                "selections_correct": graded["selections_correct"],
                "selections_graded": graded["selections_graded"],
                "markets": graded["markets"],
                # "flashscore" = a real match from the live feed; "seed" = the
                # built-in simulated dataset. The UI labels the difference so a
                # generated scoreline is never presented as a real one.
                "source": fx.source,
                "is_real": fx.source == "flashscore",
                "sportybet_url": _sportybet_result_url(fx),
                "flashscore_url": settings.flashscore_base_url,
            }
        )
        if len(rows) >= limit:
            break
    db.commit()
    wins = sum(1 for r in rows if r["hit"] is True)
    losses = sum(1 for r in rows if r["hit"] is False)
    graded_total = wins + losses
    return {
        "total": len(rows),
        "window_days": days,
        "wins": wins,
        "losses": losses,
        "hit_rate": round(wins / graded_total, 3) if graded_total else 0.0,
        "live_url": _sportybet_live_url(),
        "items": rows,
    }


def _find_value_bets(
    db: Session,
    min_edge: float | None = None,
    min_odds: float = 1.3,
    max_odds: float = 12.0,
    limit: int = 40,
    source: str | None = None,
    competition_id: int | None = None,
) -> dict:
    """Plain-function core of /value-bets so internal callers can reuse it."""
    threshold = min_edge if min_edge is not None else settings.value_edge_threshold
    # Same rule as /tips: an upcoming REAL fixture, so a value bet can never be
    # claimed on a match that has already been played or was never real.
    stmt = _fixture_query().where(_upcoming_filter())
    if source:
        stmt = stmt.where(Fixture.source == source)
    # Narrows the scan to one competition. Applied to the QUERY rather than to
    # the finished list because the model call below is the expensive part: a
    # league filter that still priced every fixture on the planet would make a
    # sidebar click as slow as the unfiltered page.
    comp_ids = _competition_id_set(db, competition_id)
    if comp_ids is not None:
        stmt = stmt.where(Fixture.competition_id.in_(comp_ids))
    fixtures = db.scalars(stmt.order_by(Fixture.kickoff)).all()

    # Prefer real matches, exactly as /tips does. The seeded dataset ships with
    # SIMULATED prices, so a value bet on a seed fixture is the model beating a
    # book we invented -- a double-digit "edge" against no real market. Once the
    # live feed has real fixtures, value must come from those or not be shown.
    if source is None:
        real = [fx for fx in fixtures if fx.source == "flashscore"]
        if real:
            fixtures = real
    out = []
    for fx in fixtures:
        p = _get_or_cache_prediction(db, fx)
        for sel in flat_selections(p):
            # A self-priced fixture has edge None and value False, so it is
            # excluded here; the explicit None check keeps that from depending
            # on the two flags staying in step.
            if not sel["value"] or sel["odds"] is None or sel["edge"] is None:
                continue
            if not (min_odds <= sel["odds"] <= max_odds):
                continue
            if sel["edge"] < threshold:
                continue
            implied = implied_probability(sel["odds"]) or 0
            out.append(
                {
                    "fixture_id": fx.id,
                    "kickoff": fx.kickoff,
                    "competition": fx.competition.label,
                    "home_team": fx.home_team.display_name,
                    "away_team": fx.away_team.display_name,
                    # Provenance, so the UI can never present a simulated fixture
                    # as a real one -- the same contract /tips honours.
                    "source": fx.source,
                    "is_real": fx.source == "flashscore",
                    "market": sel["market"],
                    "market_name": sel["market_name"],
                    "selection": sel["key"],
                    "selection_label": sel["label"],
                    "probability": sel["probability"],
                    "model_probability": sel["model_probability"],
                    "odds": sel["odds"],
                    "implied_probability": round(implied, 4),
                    "fair_odds": sel["fair_odds"],
                    "edge": sel["edge"],
                    "expected_value": round(sel["probability"] * sel["odds"] - 1, 4),
                    "kelly_stake": kelly_fraction(sel["probability"], sel["odds"]),
                    "confidence": p["confidence"],
                }
            )

    out.sort(key=lambda x: (x["edge"], x["expected_value"]), reverse=True)
    return {"total": len(out), "items": out[:limit]}

@router.get("/value-bets", tags=["predictions"])
def value_bets(
    db: Session = Depends(get_db),
    min_edge: float = Query(None),
    min_odds: float = Query(1.3, ge=1.0),
    max_odds: float = Query(12.0, ge=1.0),
    limit: int = Query(40, ge=1, le=200),
    competition_id: int | None = None,
    source: str | None = Query(
        None,
        pattern="^(seed|flashscore)$",
        description="Force one source; by default real fixtures are preferred",
    ),
):
    """Selections where the model's probability beats the de-vigged market."""
    return _find_value_bets(
        db,
        min_edge=min_edge,
        min_odds=min_odds,
        max_odds=max_odds,
        limit=limit,
        source=source,
        competition_id=competition_id,
    )


@router.get("/value-bets/summary", tags=["predictions"])
def value_bets_summary(db: Session = Depends(get_db)):
    """Compact value-bet counts, for dashboard tiles."""
    data = _find_value_bets(db, limit=500)
    return {
        "total": data["total"],
        "best_edge": data["items"][0]["edge"] if data["items"] else 0.0,
        "avg_edge": (
            round(sum(i["edge"] for i in data["items"]) / len(data["items"]), 4)
            if data["items"]
            else 0.0
        ),
    }


@router.get("/markets", tags=["predictions"])
def market_index():
    # The market taxonomy: every category a book publishes, and whether the
    # engine can price it.
    #
    # Exactly three are model-priced (1X2, Over/Under, GG/NG); the rest are
    # re-expressions of the same scoreline matrix and carry no model claim. The
    # list is served from the engine's own taxonomy rather than hardcoded in the
    # UI, so the badge and the model can never drift apart.
    cats = market_taxonomy.category_payload()
    return {
        "categories": cats,
        "model_priced": [c["key"] for c in cats if c["model_priced"]],
        "model_priced_count": sum(1 for c in cats if c["model_priced"]),
        "total": len(cats),
    }

@router.post("/fixtures/{fixture_id}/track", tags=["predictions"])
def track_tip(
    fixture_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """Save the model's headline pick so the public record can be verified."""
    fx = _get_fixture_or_404(db, fixture_id)
    p = _get_or_cache_prediction(db, fx)

    existing = db.scalars(
        select(TrackedTip).where(
            TrackedTip.fixture_id == fixture_id,
            TrackedTip.market == p["best_market"],
            TrackedTip.selection == p["best_selection"],
        )
    ).first()
    if existing:
        return {"status": "exists", "tip_id": existing.id, "record": _tip_record(db)}

    tip = TrackedTip(
        fixture_id=fixture_id,
        market=p["best_market"],
        selection=p["best_selection"],
        odds=p["best_odds"] or round(1 / p["best_probability"], 2),
        probability=p["best_probability"],
        edge=p["best_edge"],
        confidence=p["confidence"],
        stake=round(kelly_fraction(p["best_probability"], p["best_odds"] or 2.0) or 10.0, 2),
    )
    db.add(tip)
    db.commit()
    return {"status": "tracked", "tip_id": tip.id, "record": _tip_record(db)}


def _tip_record(db: Session) -> dict:
    tips = db.scalars(select(TrackedTip)).all()
    settled = [t for t in tips if t.status in ("won", "lost")]
    won = [t for t in settled if t.status == "won"]
    staked = sum(t.stake for t in settled)
    profit = sum(t.profit for t in settled)

    by_market: dict[str, dict] = {}
    for t in settled:
        row = by_market.setdefault(t.market, {"market": t.market, "tips": 0, "won": 0, "profit": 0.0, "staked": 0.0})
        row["tips"] += 1
        row["won"] += 1 if t.status == "won" else 0
        row["profit"] = round(row["profit"] + t.profit, 2)
        row["staked"] = round(row["staked"] + t.stake, 2)
    for row in by_market.values():
        row["strike_rate"] = round(row["won"] / row["tips"], 3) if row["tips"] else 0.0
        row["roi"] = round(row["profit"] / row["staked"], 3) if row["staked"] else 0.0

    return {
        "total_tips": len(tips),
        "settled": len(settled),
        "won": len(won),
        "lost": len(settled) - len(won),
        "pending": len(tips) - len(settled),
        "strike_rate": round(len(won) / len(settled), 3) if settled else 0.0,
        "roi": round(profit / staked, 3) if staked else 0.0,
        "profit": round(profit, 2),
        "staked": round(staked, 2),
        "avg_odds": round(sum(t.odds for t in tips) / len(tips), 2) if tips else 0.0,
        "by_market": sorted(by_market.values(), key=lambda r: r["tips"], reverse=True),
    }


@router.get("/record", response_model=TipRecordOut, tags=["predictions"])
def public_record(db: Session = Depends(get_db)):
    """Transparent running record of every tip the model has published."""
    return _tip_record(db)


# --------------------------------------------------------------------------
# stats, standings, team pages
# --------------------------------------------------------------------------

@router.get("/stats/overview", response_model=StatsOverviewOut, tags=["stats"])
def stats_overview(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    total = db.scalar(select(func.count()).select_from(Fixture)) or 0
    upcoming = db.scalar(
        select(func.count()).select_from(Fixture).where(Fixture.status.in_(["scheduled", "live"]))
    ) or 0
    finished = db.scalar(
        select(func.count()).select_from(Fixture).where(Fixture.status == "finished")
    ) or 0

    # backtest the model on finished matches where we have odds
    finished_fixtures = db.scalars(
        _fixture_query()
        .where(Fixture.status == "finished", Fixture.odds_home.is_not(None))
        .order_by(Fixture.kickoff.desc())
        .limit(400)
    ).all()

    correct = graded = 0
    for fx in finished_fixtures:
        if fx.home_goals is None or fx.away_goals is None:
            continue
        p = predict_fixture(db, fx, include_context=False)
        outcomes = {"home": p["home_win"], "draw": p["draw"], "away": p["away_win"]}
        pick = max(outcomes, key=lambda k: outcomes[k])
        actual = "home" if fx.home_goals > fx.away_goals else ("away" if fx.home_goals < fx.away_goals else "draw")
        graded += 1
        correct += 1 if pick == actual else 0

    value_count = _find_value_bets(db, limit=500)["total"]

    upcoming_fixtures = db.scalars(
        _fixture_query().where(Fixture.status.in_(["scheduled", "live"])).limit(60)
    ).all()
    confs = [_get_or_cache_prediction(db, fx)["confidence"] for fx in upcoming_fixtures]

    return {
        "fixtures_total": total,
        "fixtures_upcoming": upcoming,
        "fixtures_finished": finished,
        "competitions": db.scalar(select(func.count()).select_from(Competition)) or 0,
        "teams": db.scalar(select(func.count()).select_from(Team)) or 0,
        "value_bets": value_count,
        "avg_confidence": round(sum(confs) / len(confs), 1) if confs else 0.0,
        "model_accuracy": round(correct / graded, 3) if graded else None,
        "record": _tip_record(db),
    }


@router.get("/standings", tags=["stats"])
def standings(
    competition_id: int = Query(...),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(Standing)
        .options(selectinload(Standing.team))
        .where(Standing.competition_id == competition_id)
        .order_by(Standing.position)
    ).all()
    return {
        "competition_id": competition_id,
        "table": [
            {
                "position": r.position,
                "team_id": r.team_id,
                "team": r.team.display_name,
                "played": r.played,
                "won": r.won,
                "drawn": r.drawn,
                "lost": r.lost,
                "goals_for": r.goals_for,
                "goals_against": r.goals_against,
                "goal_diff": r.goals_for - r.goals_against,
                "points": r.points,
                "form": r.form,
            }
            for r in rows
        ],
    }


@router.get("/teams/{team_id}/profile", tags=["stats"])
def team_profile(team_id: int, db: Session = Depends(get_db)):
    team = db.scalars(select(Team).where(Team.id == team_id)).first()
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")

    comp = db.scalars(select(Competition).where(Competition.id == team.competition_id)).first()
    league_avg = comp.avg_goals if comp else settings.league_avg_goals

    stats = collect_team_stats(db, team_id)
    stats.name = team.display_name
    ratings = estimate_ratings(stats, league_avg)

    recent = db.scalars(
        _fixture_query()
        .where(
            Fixture.status == "finished",
            (Fixture.home_team_id == team_id) | (Fixture.away_team_id == team_id),
        )
        .order_by(Fixture.kickoff.desc())
        .limit(10)
    ).all()

    upcoming = db.scalars(
        _fixture_query()
        .where(
            _upcoming_filter(),
            (Fixture.home_team_id == team_id) | (Fixture.away_team_id == team_id),
        )
        .order_by(Fixture.kickoff)
        .limit(5)
    ).all()

    return {
        "team": {"id": team.id, "name": team.name, "short_name": team.short_name, "logo": team.logo},
        "competition": _competition_out(comp) if comp else None,
        "stats": stats.to_form_entry(),
        "ratings": {
            "attack": round(ratings.attack, 3),
            "defence": round(ratings.defence, 3),
            "league_average_goals": league_avg,
            "note": "1.00 = league average. Attack > 1 scores more than average; defence > 1 concedes more.",
        },
        "recent": [_fixture_out(fx) for fx in recent],
        "upcoming": [_fixture_out(fx, _get_or_cache_prediction(db, fx)) for fx in upcoming],
    }


# --------------------------------------------------------------------------
# auth + favourites
# --------------------------------------------------------------------------

@router.post("/auth/register", response_model=TokenOut, tags=["auth"])
def register(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    if db.scalars(select(User).where(User.email == payload.email)).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    if db.scalars(select(User).where(User.username == payload.username)).first():
        raise HTTPException(status_code=409, detail="Username taken")

    ip = _client_ip(request)
    # Registration NEVER grants admin. An earlier revision promoted the first
    # account to register ("so a fresh install has a way in"), which is a
    # privilege-escalation hole: on any database whose user table is empty -- a
    # fresh install, or one whose users were cleared -- whoever registers first
    # becomes an administrator, with no invite, secret or operator action. It
    # also contradicted this project's own stated model (see the README: there is
    # deliberately no bootstrap endpoint, and the first admin is promoted in the
    # database).
    #
    # The way in is `UPDATE users SET is_admin = 1 WHERE id = ...`, the same path
    # the README documents; after that the Users tab promotes and demotes.
    # qa/design-check.py pins this: a freshly-registered caller must be refused by
    # every /admin route, which the old behaviour made impossible to assert.
    user = User(
        email=payload.email,
        username=payload.username,
        hashed_password=hash_password(payload.password),
        is_admin=False,
        signup_ip=ip,
        last_ip=ip,
        last_login_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
        login_count=1,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"access_token": create_access_token(user), "user": user}

@router.post("/auth/login", response_model=TokenOut, tags=["auth"])
def login(
    payload: UserLogin,
    request: Request,
    db: Session = Depends(get_db),
):
    user = db.scalars(select(User).where(User.email == payload.email)).first()
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    # A blocked account cannot obtain a token at all.
    if user.status == "blocked":
        raise HTTPException(
            status_code=403,
            detail=user.status_reason or "Your account has been blocked by an administrator.",
        )

    # A kicked session clears on a successful re-login: that is the difference
    # between a kick (force out now) and a block (stay out).
    if user.status == "kicked":
        user.status = "active"

    user.last_login_at = datetime.now(timezone.utc)
    user.last_seen_at = user.last_login_at
    user.last_ip = _client_ip(request)
    user.login_count = (user.login_count or 0) + 1
    db.commit()
    db.refresh(user)
    return {"access_token": create_access_token(user), "user": user}

@router.post("/auth/logout", tags=["auth"])
def logout(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Best-effort logout: stamps the time so activity views are accurate.

    The JWT itself stays valid until it expires, which is the normal trade-off
    for stateless tokens; use kick or block to end a session server-side.
    """
    user.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "ok"}


@router.get("/auth/me", response_model=UserOut, tags=["auth"])
def me(user: User = Depends(get_current_user)):
    return user


@router.get("/favourites", tags=["auth"])
def list_favourites(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    favs = db.scalars(
        select(Favourite).options(selectinload(Favourite.team)).where(Favourite.user_id == user.id)
    ).all()
    return [
        {"id": f.id, "team_id": f.team_id, "team": f.team.display_name, "logo": f.team.logo}
        for f in favs
    ]


@router.post("/favourites/{team_id}", tags=["auth"])
def add_favourite(
    team_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not db.scalars(select(Team).where(Team.id == team_id)).first():
        raise HTTPException(status_code=404, detail="Team not found")
    if db.scalars(
        select(Favourite).where(Favourite.user_id == user.id, Favourite.team_id == team_id)
    ).first():
        return {"status": "exists"}
    db.add(Favourite(user_id=user.id, team_id=team_id))
    db.commit()
    return {"status": "added"}


@router.delete("/favourites/{team_id}", tags=["auth"])
def remove_favourite(
    team_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    fav = db.scalars(
        select(Favourite).where(Favourite.user_id == user.id, Favourite.team_id == team_id)
    ).first()
    if fav is None:
        raise HTTPException(status_code=404, detail="Not in favourites")
    db.delete(fav)
    db.commit()
    return {"status": "removed"}


@router.post("/admin/settle", tags=["admin"])
def settle_fixtures(db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    """Settle tracked tips for all finished fixtures (called by a cron in prod)."""
    finished = db.scalars(
        select(Fixture).where(Fixture.status == "finished").order_by(Fixture.kickoff.desc()).limit(100)
    ).all()
    total = sum(apply_result_to_tips(db, fx) for fx in finished)
    _audit(db, admin, "settle", None, f"tips_settled={total}")
    return {"tips_settled": total, "record": _tip_record(db)}


@router.post("/admin/seed", tags=["admin"])
def run_seed(force: bool = False, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    """Regenerate the demo dataset.

    Admin-only, and `force` is now required. Seeding overwrites populated tables,
    so a bare call was previously an unauthenticated way to destroy the demo
    data -- the `tags=["admin"]` label documented an intent nothing enforced.
    """
    from .seed import seed as do_seed
    if not force:
        raise HTTPException(
            status_code=400,
            detail="Refusing to seed without force=true: seeding overwrites existing rows.",
        )
    result = do_seed(db, force=force)
    _audit(db, admin, "seed", None, json.dumps(result, default=str)[:500])
    return result



# --------------------------------------------------------------------------
# admin: user management
# --------------------------------------------------------------------------
# Every route here depends on get_current_admin, so a non-admin gets a 403 and
# an anonymous caller a 401. Moderation is enforced in security.get_current_user
# as well, which is what makes a kick/block take effect on the next request
# rather than at the token's expiry.

@router.get("/admin/stats", response_model=AdminStatsOut, tags=["admin"])
def admin_stats(db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    """Headline numbers for the admin dashboard."""
    now = datetime.now(timezone.utc)

    def count_users(**where) -> int:
        stmt = select(func.count()).select_from(User)
        for col, val in where.items():
            stmt = stmt.where(getattr(User, col) == val)
        return db.scalar(stmt) or 0

    since = lambda days: now - timedelta(days=days)  # noqa: E731
    new_since = lambda days: db.scalar(  # noqa: E731
        select(func.count()).select_from(User).where(User.created_at >= since(days))
    ) or 0

    # signups per day for the last 14 days
    by_day: list[dict] = []
    all_recent = db.scalars(select(User).where(User.created_at >= since(14))).all()
    buckets: dict[str, int] = {}
    for u in all_recent:
        created = u.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        buckets[created.strftime("%Y-%m-%d")] = buckets.get(created.strftime("%Y-%m-%d"), 0) + 1
    for i in range(13, -1, -1):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        by_day.append({"date": day, "count": buckets.get(day, 0)})

    every_user = db.scalars(select(User)).all()
    return {
        "total_users": len(every_user),
        "active_users": sum(1 for u in every_user if u.status == "active"),
        "blocked_users": sum(1 for u in every_user if u.status == "blocked"),
        "kicked_users": sum(1 for u in every_user if u.status == "kicked"),
        "admins": sum(1 for u in every_user if u.is_admin),
        "online_now": sum(1 for u in every_user if u.is_online),
        "new_today": db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.created_at >= now.replace(hour=0, minute=0, second=0, microsecond=0))
        ) or 0,
        "new_7d": new_since(7),
        "new_30d": new_since(30),
        "total_tracked_tips": db.scalar(select(func.count()).select_from(TrackedTip)) or 0,
        "signups_by_day": by_day,
    }


@router.get("/admin/users", response_model=AdminUserListOut, tags=["admin"])
def admin_list_users(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
    q: str | None = Query(None, description="search email / username"),
    status_filter: str | None = Query(None, alias="status", pattern="^(active|blocked|kicked)$"),
    role: str | None = Query(None, pattern="^(admin|user|premium)$"),
    sort: str = Query("newest", pattern="^(newest|oldest|username|last_seen|logins)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
):
    """Every user, with the fields the panel needs to act on them."""
    stmt = select(User)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(User.email.ilike(like), User.username.ilike(like)))
    if status_filter:
        stmt = stmt.where(User.status == status_filter)
    if role == "admin":
        stmt = stmt.where(User.is_admin.is_(True))
    elif role == "premium":
        stmt = stmt.where(User.is_premium.is_(True))
    elif role == "user":
        stmt = stmt.where(User.is_admin.is_(False))

    order = {
        "newest": User.created_at.desc(),
        "oldest": User.created_at.asc(),
        "username": User.username.asc(),
        "last_seen": User.last_seen_at.desc(),
        "logins": User.login_count.desc(),
    }[sort]

    all_rows = db.scalars(stmt.order_by(order)).all()
    total = len(all_rows)
    pages = max(1, math.ceil(total / page_size))
    start = (page - 1) * page_size
    window = all_rows[start : start + page_size]

    every = db.scalars(select(User)).all()
    counts = {
        "total": len(every),
        "active": sum(1 for u in every if u.status == "active"),
        "blocked": sum(1 for u in every if u.status == "blocked"),
        "kicked": sum(1 for u in every if u.status == "kicked"),
        "online": sum(1 for u in every if u.is_online),
        "admins": sum(1 for u in every if u.is_admin),
        "premium": sum(1 for u in every if u.is_premium),
    }

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "counts": counts,
        "items": [_admin_user_out(db, u) for u in window],
    }


@router.get("/admin/users/{user_id}", response_model=AdminUserOut, tags=["admin"])
def admin_get_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    user = db.scalars(select(User).where(User.id == user_id)).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return _admin_user_out(db, user)


def _target_user(db: Session, user_id: int, admin: User) -> User:
    """Fetch a moderation target, refusing self-moderation.

    Guarding self-targeting here stops the obvious footgun of an admin blocking
    themselves and locking everyone out of the panel.
    """
    user = db.scalars(select(User).where(User.id == user_id)).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(
            status_code=400,
            detail="You cannot moderate your own account. Ask another administrator.",
        )
    return user


def _set_status(db: Session, user: User, status: str, reason: str | None) -> None:
    user.status = status
    user.status_reason = reason
    user.status_changed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)


@router.post("/admin/users/{user_id}/block", response_model=AdminActionResult, tags=["admin"])
def admin_block_user(
    user_id: int,
    payload: AdminActionIn | None = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Block a user: existing sessions die and they cannot sign back in."""
    user = _target_user(db, user_id, admin)
    reason = (payload.reason if payload else None) or "Blocked by an administrator."
    _set_status(db, user, "blocked", reason)
    return {
        "status": "blocked",
        "detail": f"{user.username} has been blocked and signed out.",
        "user": _admin_user_out(db, user),
    }


@router.post("/admin/users/{user_id}/unblock", response_model=AdminActionResult, tags=["admin"])
def admin_unblock_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    user = _target_user(db, user_id, admin)
    _set_status(db, user, "active", None)
    return {
        "status": "active",
        "detail": f"{user.username} can sign in again.",
        "user": _admin_user_out(db, user),
    }


@router.post("/admin/users/{user_id}/kick", response_model=AdminActionResult, tags=["admin"])
def admin_kick_user(
    user_id: int,
    payload: AdminActionIn | None = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Kick a user out *now*, but let them sign back in.

    Distinct from a block: this ends the current session immediately without
    making the ban permanent, which is the right tool for a disruptive user who
    has not earned a ban.
    """
    user = _target_user(db, user_id, admin)
    reason = (payload.reason if payload else None) or "Your session was ended by an administrator."
    _set_status(db, user, "kicked", reason)
    return {
        "status": "kicked",
        "detail": f"{user.username} was signed out. They may sign in again.",
        "user": _admin_user_out(db, user),
    }


@router.post(
    "/admin/users/{user_id}/role", response_model=AdminActionResult, tags=["admin"]
)
def admin_set_role(
    user_id: int,
    is_admin: bool = Query(...),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Grant or revoke administrator rights."""
    user = _target_user(db, user_id, admin)
    user.is_admin = is_admin
    db.commit()
    db.refresh(user)
    return {
        "status": "ok",
        "detail": f"{user.username} is {'now an administrator' if is_admin else 'no longer an administrator'}.",
        "user": _admin_user_out(db, user),
    }


@router.post(
    "/admin/users/{user_id}/premium", response_model=AdminActionResult, tags=["admin"]
)
def admin_set_premium(
    user_id: int,
    is_premium: bool = Query(...),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    user = _target_user(db, user_id, admin)
    user.is_premium = is_premium
    db.commit()
    db.refresh(user)
    return {
        "status": "ok",
        "detail": f"{user.username} premium {'enabled' if is_premium else 'disabled'}.",
        "user": _admin_user_out(db, user),
    }


@router.delete("/admin/users/{user_id}", response_model=AdminActionResult, tags=["admin"])
def admin_delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Permanently delete a user and their favourites."""
    user = _target_user(db, user_id, admin)
    name = user.username
    db.delete(user)
    db.commit()
    return {"status": "deleted", "detail": f"{name} was permanently deleted.", "user": None}


@router.post("/admin/users/bulk", tags=["admin"])
def admin_bulk_action(
    action: str = Query(..., pattern="^(block|unblock|kick)$"),
    user_ids: list[int] = Query(..., description="repeat the parameter per user"),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Apply the same moderation action to several users at once."""
    done, skipped = [], []
    for uid in user_ids:
        try:
            user = _target_user(db, uid, admin)
        except HTTPException:
            skipped.append(uid)
            continue
        if action == "block":
            _set_status(db, user, "blocked", "Blocked by an administrator.")
        elif action == "unblock":
            _set_status(db, user, "active", None)
        else:
            _set_status(db, user, "kicked", "Session ended by an administrator.")
        done.append(user.username)
    return {"action": action, "applied_to": done, "skipped": skipped, "count": len(done)}


# --------------------------------------------------------------------------
# Over/Under line selection
# --------------------------------------------------------------------------

@router.get("/fixtures/{fixture_id}/ou-lines", response_model=list[OuLineOut], tags=["predictions"])
def fixture_ou_line_list(fixture_id: int, db: Session = Depends(get_db)):
    """Every Over/Under line available for a fixture.

    Used by the line switcher on the match page: it returns the whole board in
    one call so changing line is a local re-render, not a new round trip.
    """
    fx = _get_fixture_or_404(db, fixture_id)
    prediction = _get_or_cache_prediction(db, fx)
    available = fixture_ou_odds(fx)

    out: list[dict] = []
    for line in OU_LINES:
        over_odd, under_odd = available.get(line, (None, None))
        if over_odd is None and under_odd is None:
            continue
        market = next(
            (m for m in prediction["markets"] if m["key"] == ou_market_key(line)), None
        )
        over_sel = under_sel = None
        if market:
            over_sel = next((s for s in market["selections"] if s["key"] == "over"), None)
            under_sel = next((s for s in market["selections"] if s["key"] == "under"), None)
        out.append(
            {
                "line": line,
                "market_key": ou_market_key(line),
                "market_name": ou_market_name(line),
                "over_odds": over_odd,
                "under_odds": under_odd,
                "over_probability": over_sel["probability"] if over_sel else None,
                "under_probability": under_sel["probability"] if under_sel else None,
                "expected_total_goals": prediction["expected_total_goals"],
                "is_main": line == MAIN_OU_LINE,
            }
        )
    return out


@router.get("/fixtures/{fixture_id}/ou-lines/{line}", tags=["predictions"])
def fixture_ou_line_detail(
    fixture_id: int,
    line: str,
    db: Session = Depends(get_db),
):
    """A single Over/Under line with both selections fully priced.

    ``line`` accepts any spelling: 2.5, ou25, over2.5, ou2_5, "Under 3.5".
    """
    resolved = resolve_ou_line(line)
    if resolved is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported line {line!r}. Available: {', '.join(str(l) for l in OU_LINES)}",
        )

    fx = _get_fixture_or_404(db, fixture_id)
    prediction = _get_or_cache_prediction(db, fx)
    key = ou_market_key(resolved)
    market = next((m for m in prediction["markets"] if m["key"] == key), None)
    if market is None:
        raise HTTPException(
            status_code=404, detail=f"No odds available for the {resolved} line on this fixture"
        )

    available = fixture_ou_odds(fx)
    over_odd, under_odd = available.get(resolved, (None, None))
    return {
        "fixture_id": fixture_id,
        "line": resolved,
        "market_key": key,
        "market_name": ou_market_name(resolved),
        "over_odds": over_odd,
        "under_odds": under_odd,
        "expected_total_goals": prediction["expected_total_goals"],
        "selections": market["selections"],
        "available_lines": [
            {"line": l, "market_key": ou_market_key(l), "is_main": l == MAIN_OU_LINE}
            for l in OU_LINES
            if l in available
        ],
    }


# ---------------------------------------------------------------------------
# Site design
# ---------------------------------------------------------------------------
#
# The look of the site is operator-editable: colours, hover behaviour, layout and
# corner radius. See `app/design.py` for why the token list is fixed and how the
# values are validated.
#
# The read endpoint is PUBLIC on purpose -- a browser cannot render the site
# without it, and it contains nothing private. Writing is admin-only.

#: The single row key the design lives under.
DESIGN_KEY = "site_design"


def _audit(
    db: Session,
    admin: User | None,
    action: str,
    target: str | None = None,
    detail: str | None = None,
) -> None:
    """Record an admin action. Never raises.

    An audit log that can break the action it is recording is worse than no log,
    so a failure here is swallowed -- the action itself already succeeded and the
    caller must not be told otherwise.
    """
    try:
        db.add(
            AdminAuditLog(
                actor=getattr(admin, "username", None) or "system",
                action=action,
                target=target,
                detail=detail,
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001 -- audit must never break the action
        db.rollback()


def _load_design(db: Session) -> tuple[dict, SiteSetting | None]:
    """The stored design and its row, or the defaults when nothing is stored."""
    row = db.scalars(select(SiteSetting).where(SiteSetting.key == DESIGN_KEY)).first()
    if row is None or not row.value:
        return site_design.default_design(), row
    try:
        stored = json.loads(row.value)
    except (TypeError, ValueError):
        # A corrupt row must not take the site down with it: fall back to the
        # shipped design and let the operator overwrite it.
        return site_design.default_design(), row
    return site_design.normalise(stored), row


@router.get("/site/design", response_model=SiteDesignOut, tags=["site"])
def get_site_design(db: Session = Depends(get_db)):
    """The design the site should render with. Public.

    Normalised on the way out, so a row written by an older revision (or edited
    by hand) still produces a complete, valid design rather than a partial one
    that leaves part of the page unstyled.
    """
    design, row = _load_design(db)
    return {
        **design,
        "changed_from_default": site_design.diff_against_default(design),
        "updated_at": row.updated_at if row else None,
        "updated_by": row.updated_by if row else None,
    }


@router.get("/site/design/templates", response_model=SiteDesignTemplatesOut, tags=["site"])
def get_design_templates():
    """Every option the editor can offer. Public, and static.

    Public because it is the same list for everyone and contains no operator
    data -- the editor page fetches it once, and the validation rules the server
    enforces are then visible to the UI rather than duplicated there.
    """
    return site_design.template_payload()


@router.put("/admin/site/design", response_model=SiteDesignOut, tags=["admin"])
def update_site_design(
    payload: SiteDesignIn,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Replace the site design. Admin only.

    A partial payload merges onto the CURRENT design rather than onto the
    defaults, so an editor that sends only the colours it touched does not
    silently reset the layout.
    """
    current, row = _load_design(db)

    merged = dict(current)
    if payload.colours is not None:
        merged["colours"] = {**current["colours"], **payload.colours}
    for field in ("hover_template", "hover_intensity", "layout", "radius"):
        value = getattr(payload, field)
        if value is not None:
            merged[field] = value

    # `normalise` is the only thing that decides what is valid, and it is given
    # the current design as the fallback -- so a malformed colour is IGNORED and
    # the saved value survives, rather than the token reverting to its default
    # and the operator's existing choice being silently wiped.
    design = site_design.normalise(merged, base=current)

    if row is None:
        row = SiteSetting(key=DESIGN_KEY, value="")
        db.add(row)
    row.value = json.dumps(design)
    row.updated_by = admin.username
    db.commit()
    db.refresh(row)

    changed = site_design.diff_against_default(design)
    _audit(db, admin, "design.update", None, f"{changed} setting(s) differ from default")

    return {
        **design,
        "changed_from_default": changed,
        "updated_at": row.updated_at,
        "updated_by": row.updated_by,
    }


@router.post("/admin/site/design/reset", response_model=SiteDesignOut, tags=["admin"])
def reset_site_design(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Restore the shipped design."""
    design = site_design.default_design()
    row = db.scalars(select(SiteSetting).where(SiteSetting.key == DESIGN_KEY)).first()
    if row is None:
        row = SiteSetting(key=DESIGN_KEY, value="")
        db.add(row)
    row.value = json.dumps(design)
    row.updated_by = admin.username
    db.commit()
    db.refresh(row)
    _audit(db, admin, "design.reset")
    return {
        **design,
        "changed_from_default": 0,
        "updated_at": row.updated_at,
        "updated_by": row.updated_by,
    }


# ---------------------------------------------------------------------------
# Admin: roles, deletion and the audit trail
# ---------------------------------------------------------------------------
#
# The panel already covered block/unblock/kick and user listing. These are the
# remaining things "manage the site as an admin" needs, each with the same
# self-targeting guard the existing moderation routes use -- an admin who
# demotes or deletes themselves can lock every operator out of the panel.


@router.patch("/admin/users/{user_id}/role", response_model=AdminUserOut, tags=["admin"])
def admin_set_role(
    user_id: int,
    payload: AdminRoleIn,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Grant or revoke admin, or toggle premium."""
    user = _target_user(db, user_id, admin)
    before = (user.is_admin, user.is_premium)
    if payload.is_admin is not None:
        user.is_admin = payload.is_admin
    if payload.is_premium is not None:
        user.is_premium = payload.is_premium
    db.commit()
    db.refresh(user)
    _audit(
        db,
        admin,
        "user.role",
        user.username,
        f"is_admin {before[0]}->{user.is_admin}, is_premium {before[1]}->{user.is_premium}",
    )
    return _admin_user_out(db, user)


@router.delete("/admin/users/{user_id}", tags=["admin"])
def admin_delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Delete a user and their own rows.

    Refuses to delete the last remaining admin: removing the only operator is
    unrecoverable from inside the panel, and the self-targeting guard alone does
    not cover an admin deleting a *different* final admin.
    """
    user = _target_user(db, user_id, admin)
    if user.is_admin:
        remaining = db.scalar(
            select(func.count()).select_from(User).where(
                User.is_admin.is_(True), User.id != user.id
            )
        ) or 0
        if remaining == 0:
            raise HTTPException(
                status_code=400,
                detail="This is the only administrator. Promote another before deleting it.",
            )

    username = user.username
    # Their favourites go with them; tracked tips are public record and stay.
    for fav in db.scalars(select(Favourite).where(Favourite.user_id == user.id)).all():
        db.delete(fav)
    db.delete(user)
    db.commit()
    _audit(db, admin, "user.delete", username)
    return {"status": "deleted", "detail": f"{username} was deleted.", "user": None}


@router.get("/admin/audit", response_model=list[AdminAuditOut], tags=["admin"])
def admin_audit(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
    limit: int = Query(50, ge=1, le=500),
):
    """The most recent admin actions, newest first."""
    rows = db.scalars(
        select(AdminAuditLog).order_by(AdminAuditLog.at.desc()).limit(limit)
    ).all()
    return [
        {
            "id": r.id,
            "at": r.at,
            "actor": r.actor,
            "action": r.action,
            "target": r.target,
            "detail": r.detail,
        }
        for r in rows
    ]
