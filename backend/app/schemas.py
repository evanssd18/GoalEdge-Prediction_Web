"""Pydantic response schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class CountryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    code: str | None = None


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    short_name: str | None = None
    logo: str | None = None


class CompetitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str
    label: str
    # The competition's country, so a client can group and sort leagues A-Z by
    # country rather than only by the league name. Optional because a
    # continental or unattributed competition has no single country.
    country: str | None = None
    is_cup: bool
    avg_goals: float


class MarketProbability(BaseModel):
    """A single selection inside a market, with model + market view."""

    key: str
    label: str
    probability: float          # blended probability 0..1
    model_probability: float    # pure statistical model
    market_probability: float | None = None
    fair_odds: float
    odds: float | None = None
    # blended prob - market implied prob. None when the price is the model's
    # own (no independent market), which is a different statement from 0.0.
    edge: float | None = None
    value: bool = False
    bookmaker: str | None = None
    self_priced: bool = False


class MarketOut(BaseModel):
    key: str
    name: str
    selections: list[MarketProbability]


class ScorelineOut(BaseModel):
    home_goals: int
    away_goals: int
    probability: float


class FormEntry(BaseModel):
    team_id: int
    team_name: str
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    goal_diff: int
    points: int
    form: str
    position: int | None = None
    ppg: float = 0.0
    home_ppg: float = 0.0
    away_ppg: float = 0.0
    clean_sheets: int = 0
    btts_rate: float = 0.0
    over25_rate: float = 0.0
    avg_scored: float = 0.0
    avg_conceded: float = 0.0


class TopPickOut(BaseModel):
    """One market's best selection in the five-way shortlist."""

    market: str
    market_name: str
    #: The concrete market the pick came from -- ``ou5_5`` for an Over/Under
    #: pick, so a grader can settle it off a stable key rather than the label.
    market_key: str
    label: str
    selection_key: str
    line: float | None = None
    probability: float
    model_probability: float
    fair_odds: float
    odds: float | None = None
    edge: float | None = None
    value: bool = False
    self_priced: bool = False
class PredictionOut(BaseModel):
    fixture_id: int
    generated_at: datetime
    engine: str
    model_version: str

    home_win: float
    draw: float
    away_win: float
    btts: float
    over25: float
    under25: float
    expected_home_goals: float
    expected_away_goals: float
    expected_total_goals: float

    best_pick: str
    best_market: str
    best_selection: str
    best_probability: float
    best_odds: float | None = None
    # None on a self-priced fixture: there is no market to compare against.
    best_edge: float | None = None
    best_self_priced: bool = False
    confidence: int
    confidence_label: str
    value_rating: int

    markets: list[MarketOut]
    # The five-market shortlist (1X2, O/U, BTTS, Correct Score, Handicap).
    # Must be declared here or the response model silently drops it: FastAPI
    # filters the returned dict through this schema, so a field the engine
    # produces but the schema omits never reaches the client, with no error.
    top_picks: list[TopPickOut] = []
    top_scorelines: list[ScorelineOut]
    home_form: FormEntry | None = None
    away_form: FormEntry | None = None
    head_to_head: dict | None = None
    key_factors: list[str] = []
    risk_notes: list[str] = []
    ai_summary: str | None = None


class FixtureOut(BaseModel):
    id: int
    kickoff: datetime
    status: str
    # The in-play clock label, e.g. "45+3'" — text, not a number.
    minute_label: str | None = None
    season: str
    matchday: int | None = None
    venue: str | None = None
    competition: CompetitionOut
    home_team: TeamOut
    away_team: TeamOut
    home_goals: int | None = None
    away_goals: int | None = None
    bookmaker: str | None = None
    prediction: PredictionOut | None = None


class FixtureListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    pages: int
    items: list[FixtureOut]


class ValueBetOut(BaseModel):
    fixture_id: int
    kickoff: datetime
    competition: str
    home_team: str
    away_team: str
    market: str
    market_name: str
    selection: str
    selection_label: str
    probability: float
    odds: float
    implied_probability: float
    edge: float
    expected_value: float
    kelly_stake: float
    confidence: int


class AiAnalysisOut(BaseModel):
    fixture_id: int
    provider: str
    model: str | None = None
    headline: str
    analysis: str
    betting_angle: str
    key_factors: list[str]
    risk_notes: list[str]
    cached: bool = False


class TipRecordOut(BaseModel):
    total_tips: int
    settled: int
    won: int
    lost: int
    pending: int
    strike_rate: float
    roi: float
    profit: float
    staked: float
    avg_odds: float
    by_market: list[dict]


class StatsOverviewOut(BaseModel):
    fixtures_total: int
    fixtures_upcoming: int
    fixtures_finished: int
    competitions: int
    teams: int
    value_bets: int
    avg_confidence: float
    model_accuracy: float | None = None
    record: TipRecordOut


# --- auth ---------------------------------------------------------------

class UserCreate(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=6, max_length=128)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    """A signed-in user, as the public frontend sees them.

    `is_admin` is included deliberately. It is the reader's own flag, not a list
    of who the administrators are, so it discloses nothing they do not already
    know -- while without it the client cannot know whether to offer the admin
    link, and the panel becomes unreachable for administrators. The panel's
    security does not rest on this flag; every endpoint it calls is gated
    server-side, and this only decides whether to draw the door.
    """

    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    username: str
    is_premium: bool
    is_admin: bool = False
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
# --- admin --------------------------------------------------------------

class AdminUserOut(BaseModel):
    """A user as the admin panel sees them."""

    id: int
    email: str
    username: str
    is_premium: bool
    is_admin: bool
    status: str
    status_reason: str | None = None
    status_changed_at: datetime | None = None
    created_at: datetime
    last_login_at: datetime | None = None
    last_seen_at: datetime | None = None
    login_count: int = 0
    last_ip: str | None = None
    signup_ip: str | None = None
    is_online: bool = False
    favourites: int = 0
    tracked_tips: int = 0
class AdminUserListOut(BaseModel):
    total: int
    page: int
    page_size: int
    pages: int
    counts: dict
    items: list[AdminUserOut]

class AdminActionIn(BaseModel):
    reason: str | None = Field(default=None, max_length=255)

class AdminActionResult(BaseModel):
    status: str
    detail: str
    user: AdminUserOut | None = None
class AdminStatsOut(BaseModel):
    total_users: int
    active_users: int
    blocked_users: int
    kicked_users: int
    admins: int
    online_now: int
    new_today: int
    new_7d: int
    new_30d: int
    total_tracked_tips: int
    signups_by_day: list[dict]

class OuLineOut(BaseModel):
    """One Over/Under line for a fixture."""

    line: float
    market_key: str
    market_name: str
    over_odds: float | None = None
    under_odds: float | None = None
    over_probability: float | None = None
    under_probability: float | None = None
    expected_total_goals: float
    is_main: bool = False


# --- site design ---------------------------------------------------------

class DesignTokenOut(BaseModel):
    """One editable design token, with what it is for."""

    key: str
    label: str
    default: str


class DesignTokenGroupOut(BaseModel):
    key: str
    label: str
    note: str | None = None
    tokens: list[DesignTokenOut]


class DesignTemplateOut(BaseModel):
    """A hover / layout / radius option."""

    key: str
    label: str
    note: str | None = None
    css: str | None = None
    value: str | None = None


class SiteDesignOut(BaseModel):
    """What the site currently looks like, plus every option available.

    Returned from a public endpoint, because the browser needs it to render the
    site at all -- and it carries no secrets: colours and layout choices are
    visible to every visitor by definition.
    """

    colours: dict[str, str]
    hover_template: str
    hover_intensity: str
    layout: str
    radius: str
    #: How many settings differ from the shipped design.
    changed_from_default: int = 0
    updated_at: datetime | None = None
    updated_by: str | None = None


class SiteDesignTemplatesOut(BaseModel):
    """The editor's control definitions."""

    token_groups: list[DesignTokenGroupOut]
    hover_templates: list[DesignTemplateOut]
    hover_intensities: list[DesignTemplateOut]
    layout_templates: list[DesignTemplateOut]
    radius_templates: list[DesignTemplateOut]


class SiteDesignIn(BaseModel):
    """An operator's proposed design.

    Every field is optional so a partial update is expressible, and none of them
    is trusted: ``design.normalise`` coerces whatever arrives into a valid design
    rather than rejecting the request, so a single bad colour cannot leave the
    site unstyled.
    """

    colours: dict[str, str] | None = None
    hover_template: str | None = None
    hover_intensity: str | None = None
    layout: str | None = None
    radius: str | None = None


class AdminAuditOut(BaseModel):
    id: int
    at: datetime
    actor: str
    action: str
    target: str | None = None
    detail: str | None = None


class AdminRoleIn(BaseModel):
    is_admin: bool | None = None
    is_premium: bool | None = None
