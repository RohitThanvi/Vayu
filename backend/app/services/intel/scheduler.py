"""
Background intelligence scheduler.
Polls all data sources on configurable intervals and feeds events into IntelStore.
Designed to run as a FastAPI lifespan background task.

Poll intervals (sensible defaults):
  USGS        every 5 min  (earthquakes update frequently)
  NASA FIRMS  every 15 min (fire data updates ~hourly on their end)
  GDELT       every 10 min (matches GDELT's own 15-min GKG publish cadence)
  ACLED       every 60 min (conflict data is not real-time)
  AIS bridge  every 60 sec (vessel positions; see services/intel/README or
              ais-bridge/README.md for why this is a REST poll against our
              own bridge service instead of a direct AISStream connection)
  Aircraft    every 90 sec (adsb.lol, merged snapshot across ~55 curated
              global aviation regions; free and keyless, polled via the
              same ais-bridge service as AIS — see ais-bridge/README.md)
  Wind field  every 45 min (animated wind vector grid from Open-Meteo,
              refreshed roughly as often as their forecast models update —
              see services/weather/wind_field.py)
  Purge       every 30 min (TTL cleanup)
"""

import asyncio
import logging
from datetime import datetime

from .fetchers import fetch_all_intel, fetch_usgs, fetch_firms, fetch_gdelt, fetch_acled, fetch_opensky
from .store import intel_store
from .vessel_store import vessel_store
from .aircraft_store import aircraft_store
from . import satellite_tle
from . import commodity_prices
from . import air_quality
from ..weather.wind_field import wind_field_store

import httpx

logger = logging.getLogger(__name__)

# ── Poll intervals in seconds ─────────────────────────────────────────────────
INTERVAL_USGS   = 5  * 60
INTERVAL_FIRMS  = 15 * 60
INTERVAL_GDELT  = 10 * 60
INTERVAL_ACLED  = 60 * 60
INTERVAL_AIS    = 60
INTERVAL_AIRCRAFT = 90   # OpenSky anonymous tier is rate-limited; global
                          # snapshot is heavier than AIS bridge polling, so
                          # slightly longer interval than AIS out of courtesy
                          # to the free, keyless tier
INTERVAL_WIND   = 45 * 60
INTERVAL_TLE    = 6 * 60 * 60   # matches satellite_tle.CACHE_TTL_SECONDS
INTERVAL_COMMODITIES = 3 * 60 * 60   # matches commodity_prices.CACHE_TTL_SECONDS —
                                       # Yahoo Finance's unofficial API has no hard
                                       # daily cap (unlike the Alpha Vantage source
                                       # this replaced), so this can run more often
                                       # while staying a good citizen about it
INTERVAL_AQI    = 60 * 60   # matches air_quality.CACHE_TTL_SECONDS — CPCB stations
                             # themselves only report hourly, no benefit polling tighter
INTERVAL_PURGE  = 30 * 60


class IntelScheduler:
    def __init__(
        self,
        acled_email: str = "",
        acled_password: str = "",
        ais_bridge_url: str = "",
        ais_bridge_api_key: str = "",
        opensky_client_id: str = "",
        opensky_client_secret: str = "",
        aqi_api_key: str = "",
    ):
        self.acled_email = acled_email
        self.acled_password = acled_password
        self.ais_bridge_url = ais_bridge_url.rstrip("/")
        self.ais_bridge_api_key = ais_bridge_api_key
        self.opensky_client_id = opensky_client_id
        self.opensky_client_secret = opensky_client_secret
        self.aqi_api_key = aqi_api_key
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
            asyncio.create_task(self._poll_ais(),    name="poll-ais"),
            asyncio.create_task(self._poll_aircraft(), name="poll-aircraft"),
            asyncio.create_task(self._poll_wind(),   name="poll-wind"),
            asyncio.create_task(self._poll_tle(),    name="poll-tle"),
            asyncio.create_task(self._poll_commodities(), name="poll-commodities"),
            asyncio.create_task(self._poll_aqi(), name="poll-aqi"),
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
                logger.info(f"USGS poll: {len(events)} events")
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
                logger.info(f"FIRMS poll: {len(events)} events")
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
                logger.info(f"GDELT poll: {len(events)} events")
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
                logger.info(f"ACLED poll: {len(events)} events")
            except Exception as e:
                logger.error(f"ACLED poll error: {e}")
            await asyncio.sleep(INTERVAL_ACLED)

    async def _poll_ais(self):
        if not self.ais_bridge_url:
            logger.info("AIS poll: no AIS_BRIDGE_URL configured, skipping vessel tracking")
            return
        await asyncio.sleep(30)   # small offset so not all fire at once
        headers = {"X-Bridge-Key": self.ais_bridge_api_key} if self.ais_bridge_api_key else {}
        while self._running:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(f"{self.ais_bridge_url}/vessels", headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                await vessel_store.load_snapshot(data.get("vessels", []))
                logger.debug(f"AIS poll: {data.get('count', 0)} vessels")
            except Exception as e:
                logger.error(f"AIS poll error: {e}")
            await asyncio.sleep(INTERVAL_AIS)

    async def _poll_aircraft(self):
        # Aircraft data comes from the ais-bridge service's /aircraft
        # endpoint (adsb.lol, merged across curated regions — see
        # ais-bridge/app.py and its README for why: originally OpenSky, but
        # OpenSky ConnectTimeouts from Render regardless of region, unlike
        # AIS which region-hopping did fix).
        if not self.ais_bridge_url:
            logger.info("Aircraft poll: no AIS_BRIDGE_URL configured, skipping aircraft tracking")
            return
        await asyncio.sleep(45)   # small offset so not all fire at once
        headers = {"X-Bridge-Key": self.ais_bridge_api_key} if self.ais_bridge_api_key else {}
        while self._running:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(f"{self.ais_bridge_url}/aircraft", headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                await aircraft_store.load_snapshot(data.get("aircraft", []))
                # The bridge itself may be failing against adsb.lol (rare,
                # but not impossible — different IP range, still subject to
                # its own availability) — surface that distinctly from a
                # bridge-poll failure so /sources still tells the true
                # story either way.
                if data.get("last_error"):
                    aircraft_store.record_error(f"bridge->adsb.lol: {data['last_error']}")
                else:
                    aircraft_store.record_success()
                logger.debug(f"Aircraft poll: {data.get('count', 0)} aircraft")
            except Exception as e:
                # This is a bridge-poll failure (bridge unreachable, bad
                # BRIDGE_API_KEY, etc) — distinct from the bridge's own
                # adsb.lol fetch failing, which is handled above via the
                # response body's last_error field instead of an exception.
                logger.error(f"Aircraft poll error: {type(e).__name__}: {e}")
                aircraft_store.record_error(f"{type(e).__name__}: {e}".rstrip(": "))
            await asyncio.sleep(INTERVAL_AIRCRAFT)

    async def _poll_wind(self):
        await asyncio.sleep(15)   # small offset so not all fire at once
        while self._running:
            try:
                await wind_field_store.refresh()
            except Exception as e:
                logger.error(f"wind field poll error: {e}")
            await asyncio.sleep(INTERVAL_WIND)

    async def _poll_tle(self):
        # Fetch immediately on startup (small offset so it's not first in
        # line with everything else), then on the normal interval — TLEs
        # are near-static within a 6h window so there's no benefit to a
        # tighter startup fetch the way USGS/FIRMS get one. Goes through
        # the bridge, not CelesTrak directly — see satellite_tle.py
        # module docstring for why.
        if not self.ais_bridge_url:
            logger.info("TLE poll: no AIS_BRIDGE_URL configured, skipping satellite tracking")
            return
        await asyncio.sleep(10)
        while self._running:
            try:
                count = await satellite_tle.refresh_from_bridge(self.ais_bridge_url, self.ais_bridge_api_key)
                logger.info(f"TLE poll: {count} satellites cached")
            except Exception as e:
                logger.error(f"TLE poll error: {type(e).__name__}: {e}")
            await asyncio.sleep(INTERVAL_TLE)

    async def _poll_commodities(self):
        # No API key needed — Yahoo Finance's unofficial chart API is fully
        # keyless (see commodity_prices.py for why it replaced Alpha Vantage).
        await asyncio.sleep(15)
        while self._running:
            try:
                count = await commodity_prices.refresh()
                logger.info(f"Commodity poll: {count} commodities cached")
            except Exception as e:
                logger.error(f"Commodity poll error: {type(e).__name__}: {e}")
            await asyncio.sleep(INTERVAL_COMMODITIES)

    async def _poll_aqi(self):
        # CPCB (India) real-time AQI — see air_quality.py module docstring.
        # Needs AQI_API_KEY (free, data.gov.in signup); skip cleanly if unset
        # rather than looping on a guaranteed-failing request.
        if not self.aqi_api_key:
            logger.info("AQI poll: no AQI_API_KEY configured, skipping air quality layer")
            return
        await asyncio.sleep(20)
        while self._running:
            try:
                count = await air_quality.refresh(self.aqi_api_key)
                logger.info(f"AQI poll: {count} stations cached")
            except Exception as e:
                logger.error(f"AQI poll error: {type(e).__name__}: {e}")
            await asyncio.sleep(INTERVAL_AQI)

    async def _purge_loop(self):
        while self._running:
            await asyncio.sleep(INTERVAL_PURGE)
            try:
                await intel_store.purge_expired()
                await aircraft_store.prune_stale()
            except Exception as e:
                logger.error(f"Purge error: {e}")


# ── Singleton ─────────────────────────────────────────────────────────────────
_scheduler: IntelScheduler | None = None


def get_scheduler(
    acled_email: str = "",
    acled_password: str = "",
    ais_bridge_url: str = "",
    ais_bridge_api_key: str = "",
    opensky_client_id: str = "",
    opensky_client_secret: str = "",
    aqi_api_key: str = "",
) -> IntelScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = IntelScheduler(
            acled_email=acled_email,
            acled_password=acled_password,
            ais_bridge_url=ais_bridge_url,
            ais_bridge_api_key=ais_bridge_api_key,
            opensky_client_id=opensky_client_id,
            opensky_client_secret=opensky_client_secret,
            aqi_api_key=aqi_api_key,
        )
    return _scheduler
