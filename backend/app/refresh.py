"""Daily background refresher for the Flashscore fixture source.

The requirement is that real matches — with their correct dates, kick-off times
and results — are fetched **every day**, not only when someone remembers to hit
the admin endpoint. This module runs that refresh on a timer inside the running
app, so a long-lived process stays current without an external cron.

Design notes
------------
* **No new dependency.** A tiny asyncio loop is enough; pulling in a scheduler
  package for one job would add a moving part for no gain.
* **Never breaks the request path.** The job runs in its own task and swallows
  its own failures into a log line, so a feed outage cannot take the app down.
* **Catches up after a restart.** Rather than sleeping a full interval before
  the first run, it refreshes immediately on boot and then every ``interval``.
* **Refreshes a window, not just today.** Results appear after a match ends, so
  each run re-pulls today plus the last few days to settle them.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .config import settings
from .database import SessionLocal
from .sync import price_flashscore_fixtures, sync_forward, sync_recent

log = logging.getLogger("goaledge.refresh")

#: Seconds between automatic refreshes. 30 minutes keeps kick-off times and
#: in-play scores close to live without hammering the feed.
DEFAULT_INTERVAL = 30 * 60
#: How long to let the app serve requests before the first refresh runs. The
#: refresh takes a write lock that SQLite holds against readers, so starting it
#: during boot made the first page load wait for the whole refresh.
DEFAULT_STARTUP_DELAY = 20


class DailyRefresher:
    """A cancellable asyncio task that refreshes real fixtures on a timer."""

    def __init__(
        self,
        interval_seconds: int = DEFAULT_INTERVAL,
        lookback_days: int = 2,
        forward_days: int = 7,
        startup_delay: int = DEFAULT_STARTUP_DELAY,
    ):
        self.interval = interval_seconds
        #: Seconds to wait before the FIRST refresh, so boot is not blocked by it.
        self.startup_delay = startup_delay
        self.lookback_days = lookback_days
        #: How many days AHEAD to sync, so tomorrow's card exists today. The feed
        #: only publishes about a week forward, so this is a week rather than the
        #: whole season.
        self.forward_days = forward_days
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.last_run: datetime | None = None
        self.last_result: dict | None = None
        self.runs = 0
        self.failures = 0

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())
        log.info(
            "flashscore refresher: started (every %ss, lookback %sd)",
            self.interval,
            self.lookback_days,
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown must not raise
            pass
        self._task = None
        log.info("flashscore refresher: stopped")

    # -- the loop ----------------------------------------------------------

    async def _loop(self) -> None:
        # Let the app finish starting before the first refresh. The refresh opens
        # a write transaction, and SQLite serialises writers against readers -- so
        # a run that starts the instant the server is up blocks every API request
        # for as long as it lasts, and the first page load sits on its spinner.
        # A short delay costs nothing and keeps boot responsive.
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=self.startup_delay)
        except asyncio.TimeoutError:
            pass
        while not self._stop.is_set():
            await self.run_once()
            try:
                # Interruptible sleep so stop() returns promptly.
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                continue

    async def run_once(self) -> dict:
        """Refresh in a worker thread so the event loop is never blocked."""
        try:
            result = await asyncio.to_thread(self._sync_blocking)
            self.last_run = datetime.now(timezone.utc)
            self.last_result = result
            self.runs += 1
            if result.get("error"):
                self.failures += 1
                log.warning("flashscore refresher: %s", result["error"])
            else:
                log.info("flashscore refresher: %s fixture(s) refreshed", result.get("updated", 0))
            return result
        except Exception as exc:  # noqa: BLE001 - a bad run must never kill the loop
            self.failures += 1
            log.exception("flashscore refresher failed")
            return {"error": str(exc)}

    def _sync_blocking(self) -> dict:
        db = SessionLocal()
        try:
            # history=True: the refresher is the place for the heavy per-team
            # history import, because it runs off the request path.
            outcomes = sync_recent(db, days=self.lookback_days, history=True)
            # Then the days ahead, so the Predictions page and the sidebar's
            # Today/Tomorrow tabs have tomorrow's card before tomorrow arrives.
            # `sync_recent` already priced whatever it pulled; the forward days
            # are priced by that same call only if they were in its range, so the
            # pricing pass is run once more below.
            forward = sync_forward(db, days=self.forward_days)
            price_flashscore_fixtures(db)
            outcomes = outcomes + forward
        finally:
            db.close()
        return {
            "runs": [
                {"date": o.date, "created": o.created, "updated": o.updated, "error": o.error}
                for o in outcomes
            ],
            "created": sum(o.created for o in outcomes),
            "updated": sum(o.updated for o in outcomes),
            "error": next((o.error for o in outcomes if o.error), None),
        }

    # -- introspection -----------------------------------------------------

    def status(self) -> dict:
        return {
            "running": self._task is not None and not self._task.done(),
            "interval_seconds": self.interval,
            "startup_delay": self.startup_delay,
            "lookback_days": self.lookback_days,
            "forward_days": self.forward_days,
            "runs": self.runs,
            "failures": self.failures,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "last_result": self.last_result,
        }


#: Process-wide refresher, started and stopped by the app lifespan.
refresher = DailyRefresher(
    interval_seconds=int(getattr(settings, "flashscore_refresh_seconds", DEFAULT_INTERVAL)),
    lookback_days=2,
    forward_days=int(getattr(settings, "flashscore_forward_days", 7)),
)
