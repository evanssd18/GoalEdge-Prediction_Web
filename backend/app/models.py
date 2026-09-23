"""SQLAlchemy ORM models for the GoalEdge AI prediction platform."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Country(Base):
    __tablename__ = "countries"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    code: Mapped[str | None] = mapped_column(String(8))

    competitions: Mapped[list[Competition]] = relationship(back_populates="country")


class Competition(Base):
    """A league or cup, e.g. 'England - Premier League'."""

    __tablename__ = "competitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    slug: Mapped[str] = mapped_column(String(140), unique=True, index=True)
    country_id: Mapped[int | None] = mapped_column(ForeignKey("countries.id"))
    tier: Mapped[int] = mapped_column(Integer, default=1)
    is_cup: Mapped[bool] = mapped_column(Boolean, default=False)
    # Average goals scored per team per match in this competition.
    avg_goals: Mapped[float] = mapped_column(Float, default=1.32)

    country: Mapped[Country | None] = relationship(back_populates="competitions")
    teams: Mapped[list[Team]] = relationship(back_populates="competition")
    fixtures: Mapped[list[Fixture]] = relationship(back_populates="competition")

    @property
    def label(self) -> str:
        return f"{self.country.name} - {self.name}" if self.country else self.name


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    short_name: Mapped[str | None] = mapped_column(String(40))
    slug: Mapped[str] = mapped_column(String(140), unique=True, index=True)
    competition_id: Mapped[int | None] = mapped_column(ForeignKey("competitions.id"))
    country_id: Mapped[int | None] = mapped_column(ForeignKey("countries.id"))
    logo: Mapped[str | None] = mapped_column(String(255))
    # Long-run scoring/defensive strength, 1.0 == league average.
    attack: Mapped[float] = mapped_column(Float, default=1.0)
    defence: Mapped[float] = mapped_column(Float, default=1.0)

    competition: Mapped[Competition | None] = relationship(back_populates="teams")

    @property
    def display_name(self) -> str:
        return self.short_name or self.name


class Fixture(Base):
    """A single match, past or future."""

    __tablename__ = "fixtures"
    __table_args__ = (UniqueConstraint("home_team_id", "away_team_id", "kickoff", name="uq_fixture"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"), index=True)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    kickoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    season: Mapped[str] = mapped_column(String(20), default="2025/26")
    matchday: Mapped[int | None] = mapped_column(Integer)
    venue: Mapped[str | None] = mapped_column(String(120))

    status: Mapped[str] = mapped_column(String(20), default="scheduled")  # scheduled|live|finished
    # The in-play clock as the feed reports it: a label such as "13'" or
    # "45+3'", not an integer. Stoppage time matters (45+3' is not 45'), so the
    # text is stored as-is rather than rounded into a number.
    minute_label: Mapped[str | None] = mapped_column(String(12))
    home_goals: Mapped[int | None] = mapped_column(Integer)
    away_goals: Mapped[int | None] = mapped_column(Integer)

    # Where this fixture came from. "seed" is the built-in simulated dataset;
    # "flashscore" is a real match pulled from the live feed. The two must stay
    # distinguishable: they drive different pages, and mixing real results into
    # the model's rating history would silently corrupt every prediction.
    source: Mapped[str] = mapped_column(String(20), default="seed", index=True)
    # The upstream feed's own id, so re-syncing a day updates rather than
    # duplicates the same match.
    external_id: Mapped[str | None] = mapped_column(String(64), index=True)

    # Market odds (decimal). Nullable because not every fixture has a book.
    odds_home: Mapped[float | None] = mapped_column(Float)
    odds_draw: Mapped[float | None] = mapped_column(Float)
    odds_away: Mapped[float | None] = mapped_column(Float)
    odds_over25: Mapped[float | None] = mapped_column(Float)
    odds_under25: Mapped[float | None] = mapped_column(Float)
    # Additional Over/Under lines, stored as JSON: {"0.5": [over, under], ...}
    # Kept as JSON rather than 12 columns because the set of lines a bookmaker
    # offers varies by match and changes over time.
    ou_lines_json: Mapped[str | None] = mapped_column(Text)
    odds_btts_yes: Mapped[float | None] = mapped_column(Float)
    odds_btts_no: Mapped[float | None] = mapped_column(Float)
    bookmaker: Mapped[str | None] = mapped_column(String(60), default="Bet365")

    # Cached prediction payload so we don't recompute on every request.
    prediction_json: Mapped[str | None] = mapped_column(Text)
    prediction_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    competition: Mapped[Competition] = relationship(back_populates="fixtures")
    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])

    @property
    def is_finished(self) -> bool:
        return self.status == "finished" and self.home_goals is not None


class Standing(Base):
    """Lite league table row, used for context in the AI analysis."""

    __tablename__ = "standings"

    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    season: Mapped[str] = mapped_column(String(20), default="2025/26")
    position: Mapped[int] = mapped_column(Integer)
    played: Mapped[int] = mapped_column(Integer, default=0)
    won: Mapped[int] = mapped_column(Integer, default=0)
    drawn: Mapped[int] = mapped_column(Integer, default=0)
    lost: Mapped[int] = mapped_column(Integer, default=0)
    goals_for: Mapped[int] = mapped_column(Integer, default=0)
    goals_against: Mapped[int] = mapped_column(Integer, default=0)
    points: Mapped[int] = mapped_column(Integer, default=0)
    form: Mapped[str] = mapped_column(String(10), default="")  # e.g. "WWDWL"

    team: Mapped[Team] = relationship()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # --- moderation ------------------------------------------------------
    # "active" | "blocked" | "kicked".
    #   blocked - permanently barred; login is refused.
    #   kicked  - sessions invalidated now, but may sign in again. Used as a
    #             reversible "force this person out right now".
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    status_reason: Mapped[str | None] = mapped_column(String(255))
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    login_count: Mapped[int] = mapped_column(Integer, default=0)
    last_ip: Mapped[str | None] = mapped_column(String(64))
    signup_ip: Mapped[str | None] = mapped_column(String(64))

    favourites: Mapped[list[Favourite]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_active_user(self) -> bool:
        return self.status != "blocked"

    @property
    def is_online(self) -> bool:
        if self.last_seen_at is None:
            return False
        seen = self.last_seen_at
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        return (utcnow() - seen).total_seconds() < 300


class Favourite(Base):
    """A user-tracked team or competition."""

    __tablename__ = "favourites"
    __table_args__ = (UniqueConstraint("user_id", "team_id", name="uq_fav"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="favourites")
    team: Mapped[Team] = relationship()


class TrackedTip(Base):
    """A saved tip used to build the transparent public tipster record."""

    __tablename__ = "tracked_tips"

    id: Mapped[int] = mapped_column(primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), index=True)
    market: Mapped[str] = mapped_column(String(40))
    selection: Mapped[str] = mapped_column(String(80))
    odds: Mapped[float] = mapped_column(Float)
    probability: Mapped[float] = mapped_column(Float)
    edge: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[int] = mapped_column(Integer, default=50)
    stake: Mapped[float] = mapped_column(Float, default=10.0)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|won|lost|void
    profit: Mapped[float] = mapped_column(Float, default=0.0)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    fixture: Mapped[Fixture] = relationship()


class SiteSetting(Base):
    """One operator-editable site setting, as a key/value row.

    A key/value table rather than a column per setting, because these are
    presentation choices that come and go: adding a token should be a line in
    ``design.py``, not a database migration. The value is JSON text so a design
    (a nested dict of colours plus template choices) fits in one row without the
    schema knowing its shape.

    Only settings the application asks for are read, so an orphaned row is inert
    rather than dangerous.
    """

    __tablename__ = "site_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    #: Who last changed it. Nullable because the seeded default has no author.
    updated_by: Mapped[str | None] = mapped_column(String(120), nullable=True)


class AdminAuditLog(Base):
    """Who did what in the admin panel.

    An admin panel that can block users, promote people and restyle the whole
    site needs a record of who did it -- without one, \"why is this user blocked\"
    is unanswerable. Deliberately append-only: nothing in the API updates or
    deletes a row here.
    """

    __tablename__ = "admin_audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(120), index=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    target: Mapped[str | None] = mapped_column(String(120), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
