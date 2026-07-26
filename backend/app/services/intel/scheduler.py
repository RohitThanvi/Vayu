"""
Background intelligence scheduler.
Polls all data sources on configurable intervals and feeds events into IntelStore.
Designed to run as a FastAPI lifespan background task.

Poll intervals (sensible defaults):
  USGS        every 5 min  (earthquakes update frequently)
  NASA FIRMS  every 15 min (fire data updates ~hourly on their end)
  GDELT       every 10 min (matches GDELT's own 15-min GKG publish cadence)
  ACLED       every 60 min (conflict data is not real-time)
  Purge       every 30 min (TTL cleanup)
"""

import asyncio
import logging
from datetime import datetime

from .fetchers import fetch_all_intel, fetch_usgs, fetch_firms, fetch_gdelt, fetch_acled
from .store import intel_store

import httpx

logger = logging.getLogger(__name__)

# ── Poll intervals in seconds ─────────────────────────────────────────────────
INTERVAL_USGS   = 5  * 60
INTERVAL_FIRMS  = 15 * 60
INTERVAL_GDELT  = 10 * 60
INTERVAL_ACLED  = 60 * 60
INTERVAL_PURGE  = 30 * 60


class IntelScheduler:
    def __init__(self, acled_email: str = "", acled_password: str = ""):
        self.acled_email = acled_email
        self.acled_password = acled_password
        self._tasks: list[asyncio.Task] = []
        self._running = False

    async def start(self):
        """Start all background polling tasks."""
        if self._running:
            return
        self._running = True

        # Initial fetch — populate store immediately on startup
        logger.info("Intel scheduler: initial fetch starting...")
        try:
            events = await fetch_all_intel(
                acled_email=self.acled_email,
                acled_password=self.acled_password,
                since_minutes=360,   # last 6h on first boot
            )
            await intel_store.ingest(events)
            logger.info(f"Intel scheduler: initial fetch complete — {len(events)} events loaded")
        except Exception as e:
            logger.error(f"Intel scheduler: initial fetch failed: {e}")

        # Launch per-source polling loops
        self._tasks = [
            asyncio.create_task(self._poll_usgs(),   name="poll-usgs"),
            asyncio.create_task(self._poll_firms(),  name="poll-firms"),
            asyncio.create_task(self._poll_gdelt(),  name="poll-gdelt"),
            asyncio.create_task(self._poll_acled(),  name="poll-acled"),
            asyncio.create_task(self._purge_loop(),  name="purge-loop"),
        ]
        logger.info(f"Intel scheduler: {len(self._tasks)} polling tasks started")

    async def stop(self):
        """Gracefully cancel all polling tasks."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("Intel scheduler: stopped")

    # ── Per-source loops ──────────────────────────────────────────────────────
    async def _poll_usgs(self):
        await asyncio.sleep(INTERVAL_USGS)   # offset from initial fetch
        while self._running:
            try:
                async with httpx.AsyncClient(follow_redirects=True) as client:
                    events = await fetch_usgs(client, since_minutes=10)
                await intel_store.ingest(events)
                logger.debug(f"USGS poll: {len(events)} events")
            except Exception as e:
                logger.error(f"USGS poll error: {e}")
            await asyncio.sleep(INTERVAL_USGS)

    async def _poll_firms(self):
        await asyncio.sleep(60)   # small offset so not all fire at once
        while self._running:
            try:
                async with httpx.AsyncClient(follow_redirects=True) as client:
                    events = await fetch_firms(client)
                await intel_store.ingest(events)
                logger.debug(f"FIRMS poll: {len(events)} events")
            except Exception as e:
                logger.error(f"FIRMS poll error: {e}")
            await asyncio.sleep(INTERVAL_FIRMS)

    async def _poll_gdelt(self):
        await asyncio.sleep(120)
        while self._running:
            try:
                # GKG zip files can be 15-30MB; give this a longer timeout
                async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
                    events = await fetch_gdelt(client)
                await intel_store.ingest(events)
                logger.debug(f"GDELT poll: {len(events)} events")
            except Exception as e:
                logger.error(f"GDELT poll error: {e}")
            await asyncio.sleep(INTERVAL_GDELT)

    async def _poll_acled(self):
        await asyncio.sleep(180)
        while self._running:
            try:
                async with httpx.AsyncClient(follow_redirects=True) as client:
                    events = await fetch_acled(
                        client,
                        email=self.acled_email,
                        password=self.acled_password,
                        days_back=1,
                    )
                await intel_store.ingest(events)
                logger.debug(f"ACLED poll: {len(events)} events")
            except Exception as e:
                logger.error(f"ACLED poll error: {e}")
            await asyncio.sleep(INTERVAL_ACLED)

    async def _purge_loop(self):
        while self._running:
            await asyncio.sleep(INTERVAL_PURGE)
            try:
                await intel_store.purge_expired()
            except Exception as e:
                logger.error(f"Purge error: {e}")


# ── Singleton ─────────────────────────────────────────────────────────────────
_scheduler: IntelScheduler | None = None


def get_scheduler(acled_email: str = "", acled_password: str = "") -> IntelScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = IntelScheduler(acled_email=acled_email, acled_password=acled_password)
    return _scheduler
