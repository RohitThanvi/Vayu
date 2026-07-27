"""
AISStream.io live vessel tracking client.

Unlike the other intel sources (request/response polling), AIS data is a
persistent WebSocket stream. This module maintains a long-lived connection
to aisstream.io, subscribes to a curated set of major maritime chokepoints
(rather than the whole globe, to keep bandwidth/memory sane on a free-tier
deployment), and feeds every position/static report into VesselStore.

Free registration: https://aisstream.io  (no credit card required)

Chokepoints chosen because they carry a disproportionate share of global
oil and bulk-commodity shipping, making them the most "intelligence
relevant" places to watch vessel traffic:
  - Strait of Hormuz      (~20% of global oil)
  - Strait of Malacca     (China/Asia trade artery)
  - Bab-el-Mandeb         (Red Sea / Suez approach)
  - Suez Canal
  - Strait of Gibraltar
  - Panama Canal
  - English Channel / Dover Strait
"""

import asyncio
import json
import logging
from typing import Optional

import websockets

from .vessel_store import vessel_store

logger = logging.getLogger(__name__)

AISSTREAM_WS_URL = "wss://stream.aisstream.io/v0/stream"

# [[lat1, lon1], [lat2, lon2]] bounding boxes — kept generous but bounded
CHOKEPOINTS = {
    "strait_of_hormuz":    [[24.5, 54.5], [27.5, 57.0]],
    "strait_of_malacca":   [[1.0, 100.0], [6.5, 104.5]],
    "bab_el_mandeb":       [[11.5, 42.5], [15.0, 44.5]],
    "suez_canal":          [[29.5, 32.0], [31.5, 33.0]],
    "strait_of_gibraltar": [[35.7, -6.0], [36.3, -5.0]],
    "panama_canal":        [[8.8, -80.2], [9.4, -79.4]],
    "english_channel":     [[49.8, -2.0], [51.2, 2.0]],
}

RECONNECT_DELAY_S = 10
MAX_RECONNECT_DELAY_S = 120
RATE_LIMIT_MIN_DELAY_S = 60    # floor when we get a 429 with no Retry-After header
RATE_LIMIT_MAX_DELAY_S = 600   # ceiling, in case the server asks for something huge


def _retry_after_seconds(exc: Exception) -> Optional[int]:
    """If `exc` is a 429 rejection carrying a Retry-After header, return the
    number of seconds to wait. Returns None for anything else (falls back to
    the normal exponential backoff)."""
    response = getattr(exc, "response", None)
    if response is None or getattr(response, "status_code", None) != 429:
        return None
    retry_after = None
    try:
        retry_after = response.headers.get("Retry-After")
    except Exception:
        pass
    if retry_after:
        try:
            return max(RATE_LIMIT_MIN_DELAY_S, min(int(retry_after), RATE_LIMIT_MAX_DELAY_S))
        except (TypeError, ValueError):
            pass
    # 429 with no usable Retry-After — still treat it as a real rate limit,
    # not a transient blip, and back off further than our normal schedule.
    return RATE_LIMIT_MIN_DELAY_S


class AISStreamClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        if not self.api_key:
            logger.info("AISStream: no API key configured, skipping vessel tracking")
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_forever(), name="ais-stream")
        logger.info("AISStream: vessel tracking task started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("AISStream: vessel tracking stopped")

    async def _run_forever(self):
        delay = RECONNECT_DELAY_S
        while self._running:
            try:
                await self._connect_and_stream()
                delay = RECONNECT_DELAY_S  # reset backoff on clean exit
            except asyncio.CancelledError:
                raise
            except Exception as e:
                rate_limit_delay = _retry_after_seconds(e)
                wait_s = rate_limit_delay if rate_limit_delay is not None else delay
                reason = "rate limited" if rate_limit_delay is not None else "connection error"
                logger.error(f"AISStream: {reason}: {e}, retrying in {wait_s}s")
                await asyncio.sleep(wait_s)
                delay = min(delay * 2, MAX_RECONNECT_DELAY_S)

    async def _connect_and_stream(self):
        bounding_boxes = list(CHOKEPOINTS.values())
        subscribe_message = {
            "APIKey": self.api_key,
            "BoundingBoxes": bounding_boxes,
            "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
        }

        async with websockets.connect(AISSTREAM_WS_URL, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(json.dumps(subscribe_message))
            logger.info(f"AISStream: subscribed to {len(bounding_boxes)} chokepoint regions")

            async for raw_message in ws:
                if not self._running:
                    break
                try:
                    message = json.loads(raw_message)
                except json.JSONDecodeError:
                    continue

                msg_type = message.get("MessageType")
                inner = message.get("Message", {})
                metadata = message.get("MetaData", {})
                mmsi = metadata.get("MMSI")
                if mmsi is None:
                    continue

                if msg_type == "PositionReport":
                    pr = inner.get("PositionReport", {})
                    lat = pr.get("Latitude")
                    lon = pr.get("Longitude")
                    if lat is None or lon is None:
                        continue
                    await vessel_store.update_position(
                        mmsi=mmsi,
                        lat=lat,
                        lon=lon,
                        sog=pr.get("Sog", 0.0),
                        cog=pr.get("Cog", 0.0),
                        heading=pr.get("TrueHeading"),
                    )
                    # ShipName sometimes arrives in metadata even on position reports
                    ship_name = metadata.get("ShipName")
                    if ship_name and ship_name.strip():
                        await vessel_store.update_static(mmsi=mmsi, name=ship_name)

                elif msg_type == "ShipStaticData":
                    sd = inner.get("ShipStaticData", {})
                    await vessel_store.update_static(
                        mmsi=mmsi,
                        name=sd.get("ShipName", ""),
                        ship_type=sd.get("Type"),
                        destination=sd.get("Destination", ""),
                        callsign=sd.get("CallSign", ""),
                    )


# ── Singleton ─────────────────────────────────────────────────────────────────
_ais_client: Optional[AISStreamClient] = None


def get_ais_client(api_key: str = "") -> AISStreamClient:
    global _ais_client
    if _ais_client is None:
        _ais_client = AISStreamClient(api_key=api_key)
    return _ais_client
