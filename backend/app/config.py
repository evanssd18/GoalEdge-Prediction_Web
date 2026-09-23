"""Application configuration loaded from environment variables / .env file."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "GoalEdge AI"
    app_description: str = "AI-powered football match predictions and betting tips"
    version: str = "1.0.0"

    # SQLite by default; swap DATABASE_URL for Postgres in production.
    database_url: str = f"sqlite:///{(BASE_DIR / 'goaledge.db').as_posix()}"

    # --- AI layer -------------------------------------------------------
    # If OPENAI_API_KEY is set the /ai endpoints use an LLM for the written
    # analysis. Without a key the built-in deterministic sports-writer
    # fallback is used, so the product always works offline.
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    ai_timeout_seconds: float = 30.0

    # --- Auth -----------------------------------------------------------
    jwt_secret: str = "change-me-in-production-goaledge-secret"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7

    # --- Modelling ------------------------------------------------------
    # Blend weight between the statistical model and the market (bookmaker)
    # implied probabilities. 0.0 = pure model, 1.0 = pure market.
    market_blend: float = 0.28
    # Dixon-Coles low-score correlation parameter.
    dixon_coles_rho: float = -0.06
    # League-average goals per team per match, used as the Bayesian prior.
    league_avg_goals: float = 1.32
    # Weight applied to the prior when a team has few recorded matches.
    prior_weight: float = 6.0
    # Minimum edge (model prob - market prob) to flag a value bet.
    value_edge_threshold: float = 0.045
    # --- SportyBet odds display ------------------------------------------
    # Read-only market board, shown next to (never blended into) the model's
    # own selections. Disabled by default: the board is large, so it is only
    # fetched when a deployment deliberately opts in.
    sportybet_markets_enabled: bool = False
    sportybet_base_url: str = "https://www.sportybet.com/api"
    sportybet_country: str = "ng"
    sportybet_product_id: int = 3
    sportybet_timeout_seconds: float = 8.0
    # How long a fetched market board stays fresh before it is refetched.
    sportybet_cache_seconds: int = 90
    # --- Flashscore fixture source ---------------------------------------
    # The app's own demo data is simulated. When this is enabled, real
    # matches (dates, kick-off times, statuses and results) are pulled from
    # Flashscore's public day feed and written into the same Fixture table,
    # so the predictions and the Results page grade real football.
    flashscore_enabled: bool = True
    flashscore_base_url: str = "https://www.flashscore.com.gh"
    # Public feed token the site itself sends; it is not a secret credential,
    # only a request header the endpoint expects.
    flashscore_feed_token: str = "SW9D1eZo"
    flashscore_lang: str = "en-gh"
    flashscore_timeout_seconds: float = 20.0
    # Sport id 1 is football on Flashscore.
    flashscore_sport_id: int = 1
    # Cap on matches ingested per day so a busy Saturday cannot flood the DB.
    flashscore_max_matches_per_day: int = 400
    # How many days AHEAD of today each refresh pulls. The Predictions page and
    # the sidebar's Today/Tomorrow tabs read *scheduled* fixtures, so without a
    # forward pull tomorrow's card does not exist until tomorrow. The feed only
    # publishes about a week forward, so this is a week rather than a season.
    flashscore_forward_days: int = 7
    # How often the in-process refresher re-pulls real fixtures (seconds).
    # Default 30 minutes: kick-off times and in-play scores stay close to live
    # without hammering the feed.
    flashscore_refresh_seconds: int = 1800
    cors_origins: list[str] = ["*"]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
