"""GoalEdge AI — FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from sqlalchemy import inspect, text
from .config import settings
from .database import Base, SessionLocal, engine
from .routers import router
from .seed import seed as seed_database

def _ensure_columns() -> None:
    """Add columns introduced after the database was first created.

    ``create_all`` only creates missing *tables*; it never alters an existing
    one. Without this, upgrading an install that already has a populated
    ``goaledge.db`` would raise "no such column: fixtures.source" on the first
    query. Each statement is guarded by a live inspection so it is a no-op once
    the column exists and never runs blind against a schema we did not expect.
    """
    inspector = inspect(engine)
    if "fixtures" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("fixtures")}
    additions = {
        "source": "ALTER TABLE fixtures ADD COLUMN source VARCHAR(20) DEFAULT 'seed'",
        "external_id": "ALTER TABLE fixtures ADD COLUMN external_id VARCHAR(64)",
        # The in-play clock label ("45+3'"). Kept apart from the old integer
        # `minute` column rather than renamed: a rename would need a destructive
        # migration on an existing database, and that column is now unused.
        "minute_label": "ALTER TABLE fixtures ADD COLUMN minute_label VARCHAR(12)",
    }
    with engine.begin() as conn:
        for column, ddl in additions.items():
            if column not in existing:
                conn.execute(text(ddl))
                log.info("schema: added fixtures.%s", column)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("goaledge")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    db = SessionLocal()
    try:
        result = seed_database(db)
        log.info("seed: %s", result)
    finally:
        db.close()

    # The schedule sync runs OFF the startup path, in a worker thread.
    #
    # It used to run inline here, on the reasoning that a feed outage must not
    # stop the app from serving. But an inline call only survives a feed that
    # *fails* -- one that hangs holds the lifespan open, the port is never
    # bound, and the process sits at "Waiting for application startup" forever
    # while a previously started server keeps answering on the same port. That
    # is a silent, hard-to-diagnose outage on any machine that cannot reach the
    # feed, and it is exactly the failure this comment was trying to prevent.
    #
    # `history=False`: the per-team history import costs a network call per
    # fixture, which belongs on the background refresher, not in front of the
    # first request.
    if settings.flashscore_enabled:
        import threading
        def _sync_in_background() -> None:
            bg = SessionLocal()
            try:
                from .sync import sync_recent
                for outcome in sync_recent(bg, days=2, history=False):
                    log.info("flashscore sync: %s", outcome.as_dict())
            except Exception as exc:  # noqa: BLE001 - a feed fault must not crash the app
                log.warning("flashscore sync failed: %s", exc)
            finally:
                bg.close()

        threading.Thread(target=_sync_in_background, name="boot-sync", daemon=True).start()

    # Keep real fixtures current for as long as the process lives: the daily
    # requirement is served by a timer, not only by the admin endpoint. The
    # refresher also runs the heavier history import.
    from .refresh import refresher
    if settings.flashscore_enabled:
        refresher.start()
    try:
        yield
    finally:
        await refresher.stop()


app = FastAPI(
    title=settings.app_name,
    description=settings.app_description,
    version=settings.version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/api", include_in_schema=False)
def api_index():
    return RedirectResponse(url="/docs")


# Serve the static frontend from the same origin so there is no CORS dance.
# __file__ is <root>/backend/app/main.py, so the project root is three levels up.
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    log.info("frontend served from %s", FRONTEND_DIR)
else:  # pragma: no cover
    log.warning("frontend directory not found at %s — API-only mode", FRONTEND_DIR)


@app.exception_handler(500)
def internal_error(request, exc):  # pragma: no cover
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
