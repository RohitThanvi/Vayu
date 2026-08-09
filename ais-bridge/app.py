"""
Vayu AIS Bridge
================
A small, standalone service whose only job is to hold the one persistent
AISStream.io WebSocket connection and re-expose it as a plain REST endpoint.

Why this exists: AISStream is WebSocket-only (no REST API) and enforces one
live connection per API key. When the main Vayu backend held that connection
directly on Render, Render's shared outbound IP pool got the connection
rejected with HTTP 429 at the handshake — before AISStream even reads the
API key — and swapping keys didn't help, confirming it was IP-based, not
key-based. Renegotiating fixed IPs is a paid Render feature, so instead this
bridge runs on a host with its own clean IP (e.g. Fly.io's free tier) and
holds the connection there. The main backend never talks to AISStream at
all anymore — it just polls this bridge's /vessels endpoint on a normal
HTTP interval, exactly like it already polls USGS/FIRMS/GDELT.

Deploy this as its own small service, separate from the main Vayu backend.
Required env vars:
  AISSTREAM_API_KEY   your aisstream.io key
  BRIDGE_API_KEY      a secret you invent — the backend must send it back
                       as the X-Bridge-Key header on every request. Without
                       this, anyone who finds the bridge's public URL could
                       read your feed for free. Set this before deploying
                       publicly.
"""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional

import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException

load_dotenv()  # reads .env in this directory if present — no-op in prod
                # (Fly.io/Render inject real env vars directly, .env is
                # purely a local-dev convenience and is gitignored)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("ais_bridge")

AISSTREAM_API_KEY = os.environ.get("AISSTREAM_API_KEY", "")
BRIDGE_API_KEY = os.environ.get("BRIDGE_API_KEY", "")
AISSTREAM_WS_URL = "wss://stream.aisstream.io/v0/stream"

STALE_MINUTES = 30
MAX_VESSELS = 5000
PRUNE_INTERVAL_S = 10 * 60

# Same curated chokepoints as before — kept bounded rather than subscribing
# to the whole globe, to keep memory/bandwidth sane on a free-tier host.
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
RATE_LIMIT_MIN_DELAY_S = 60
RATE_LIMIT_MAX_DELAY_S = 600


# ── Ship-type classification (mirrors backend/app/services/intel/vessel_store.py) ──
def classify_ship_type(type_code: Optional[int]) -> str:
    if type_code is None:
        return "OTHER"
    try:
        code = int(type_code)
    except (TypeError, ValueError):
        return "OTHER"
    if 80 <= code <= 89:
        return "TANKER"
    if 70 <= code <= 79:
        return "CARGO"
    if 60 <= code <= 69:
        return "PASSENGER"
    if 30 <= code <= 39:
        return "FISHING"
    return "OTHER"


CATEGORY_LABELS = {
    "TANKER": "Tanker (oil / chemical / gas)",
    "CARGO": "Cargo / Bulk Carrier (ore, grain, minerals, containers)",
    "PASSENGER": "Passenger Vessel",
    "FISHING": "Fishing Vessel",
    "OTHER": "Other / Unclassified",
}


def _retry_after_seconds(exc: Exception) -> Optional[int]:
    """If `exc` is a 429 rejection carrying a Retry-After header, return the
    number of seconds to wait. Returns None for anything else (falls back to
    plain exponential backoff).

    Handles both the legacy `websockets.connect` exception shape
    (InvalidStatusCode: status_code/headers directly on the exception) and
    the newer asyncio-client shape (InvalidStatus: status/headers nested
    under `.response`), since which one fires depends on the installed
    websockets version.
    """
    status_code = getattr(exc, "status_code", None)
    headers = getattr(exc, "headers", None)
    if status_code is None:
        response = getattr(exc, "response", None)
        if response is not None:
            status_code = getattr(response, "status_code", None)
            headers = getattr(response, "headers", None)
    if status_code != 429:
        return None
    retry_after = None
    try:
        retry_after = headers.get("Retry-After") if headers is not None else None
    except Exception:
        pass
    if retry_after:
        try:
            return max(RATE_LIMIT_MIN_DELAY_S, min(int(retry_after), RATE_LIMIT_MAX_DELAY_S))
        except (TypeError, ValueError):
            pass
    return RATE_LIMIT_MIN_DELAY_S


class VesselBuffer:
    """In-memory latest-state-per-MMSI buffer. Deliberately independent from
    the main backend's VesselStore — this service has zero import
    dependency on the main app, so it can be deployed completely on its
    own."""

    def __init__(self):
        self._vessels: dict[int, dict] = {}
        self._lock = asyncio.Lock()

    async def update_position(
        self, mmsi: int, lat: float, lon: float,
        sog: float = 0.0, cog: float = 0.0, heading: float = None,
    ):
        async with self._lock:
            v = self._vessels.get(mmsi, {"mmsi": mmsi})
            v.update({
                "lat": lat, "lon": lon, "sog": sog, "cog": cog,
                "heading": heading, "last_update": datetime.utcnow().isoformat() + "Z",
            })
            self._vessels[mmsi] = v
            if len(self._vessels) > MAX_VESSELS:
                await self._prune_locked()

    async def update_static(
        self, mmsi: int, name: str = "", ship_type: int = None,
        destination: str = "", callsign: str = "",
    ):
        async with self._lock:
            v = self._vessels.get(mmsi, {"mmsi": mmsi})
            category = classify_ship_type(ship_type)
            v.update({
                "name": name.strip() if name else v.get("name", f"MMSI {mmsi}"),
                "ship_type_code": ship_type,
                "category": category,
                "category_label": CATEGORY_LABELS.get(category, "Other"),
                "destination": destination.strip() if destination else v.get("destination", ""),
                "callsign": callsign.strip() if callsign else v.get("callsign", ""),
            })
            self._vessels[mmsi] = v

    async def _prune_locked(self):
        cutoff = datetime.utcnow() - timedelta(minutes=STALE_MINUTES)
        before = len(self._vessels)
        self._vessels = {
            mmsi: v for mmsi, v in self._vessels.items()
            if "last_update" in v and
            datetime.fromisoformat(v["last_update"].rstrip("Z")) > cutoff
        }
        pruned = before - len(self._vessels)
        if pruned:
            logger.info(f"pruned {pruned} stale vessels")

    async def prune_stale(self):
        async with self._lock:
            await self._prune_locked()

    def snapshot(self) -> list[dict]:
        return list(self._vessels.values())


vessel_buffer = VesselBuffer()


class AISBridgeClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        if not self.api_key:
            logger.warning("AISSTREAM_API_KEY not set — bridge will not connect to AISStream")
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_forever(), name="ais-bridge-stream")
        logger.info("AIS bridge: vessel tracking task started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("AIS bridge: vessel tracking stopped")

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

        async with websockets.connect(AISSTREAM_WS_URL, ping_interval=25, ping_timeout=40) as ws:
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
                    await vessel_buffer.update_position(
                        mmsi=mmsi, lat=lat, lon=lon,
                        sog=pr.get("Sog", 0.0), cog=pr.get("Cog", 0.0),
                        heading=pr.get("TrueHeading"),
                    )
                    ship_name = metadata.get("ShipName")
                    if ship_name and ship_name.strip():
                        await vessel_buffer.update_static(mmsi=mmsi, name=ship_name)

                elif msg_type == "ShipStaticData":
                    sd = inner.get("ShipStaticData", {})
                    await vessel_buffer.update_static(
                        mmsi=mmsi,
                        name=sd.get("ShipName", ""),
                        ship_type=sd.get("Type"),
                        destination=sd.get("Destination", ""),
                        callsign=sd.get("CallSign", ""),
                    )


ais_client = AISBridgeClient(api_key=AISSTREAM_API_KEY)


async def _prune_loop():
    while True:
        await asyncio.sleep(PRUNE_INTERVAL_S)
        try:
            await vessel_buffer.prune_stale()
        except Exception as e:
            logger.error(f"prune error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not BRIDGE_API_KEY:
        logger.warning(
            "BRIDGE_API_KEY not set — /vessels is UNPROTECTED. "
            "Set it before deploying this publicly."
        )
    await ais_client.start()
    prune_task = asyncio.create_task(_prune_loop())
    yield
    prune_task.cancel()
    await ais_client.stop()


app = FastAPI(title="Vayu AIS Bridge", version="1.0.0", lifespan=lifespan)


def _check_auth(x_bridge_key: Optional[str]):
    if BRIDGE_API_KEY and x_bridge_key != BRIDGE_API_KEY:
        raise HTTPException(status_code=401, detail="missing or invalid X-Bridge-Key header")


@app.get("/health")
async def health():
    return {"status": "ok", "vessel_count": len(vessel_buffer.snapshot())}


@app.get("/vessels")
async def get_vessels(x_bridge_key: Optional[str] = Header(default=None)):
    _check_auth(x_bridge_key)
    vessels = vessel_buffer.snapshot()
    return {
        "vessels": vessels,
        "count": len(vessels),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
