"""
Vayu Network Bridge (AIS + OpenSky)
====================================
A small, standalone service whose job is to hold outbound connections that
Render's datacenter IP range gets blocked on, and re-expose them as plain
REST endpoints the main Vayu backend polls over normal HTTP.

Two feeds live here:

1. AIS (AISStream.io) — WebSocket-only, one live connection per API key.
   When the main backend held that connection directly on Render, every
   attempt got rejected with HTTP 429 at the handshake — before AISStream
   even reads the API key — and regenerating the key didn't help. That
   points at Render's shared outbound IP pool getting blocked, not
   anything about the key or the code.

2. Aircraft (OpenSky Network) — same shape of problem: OpenSky's
   /states/all consistently ConnectTimeouts from Render even with valid
   OAuth2 credentials configured (ruling out an auth/rate-limit cause —
   this is a connection-level rejection, not a 4xx with a body), matching
   the same "this datacenter IP range specifically is blocked" pattern
   AIS hit first. Moving the poll here (a different host, different IP
   range — this was proven to work for AIS) is the fix.

Both feeds run in the same lightweight service since neither needs its
own dedicated worker the way AIS's one-connection-per-key constraint
might suggest — only the *AISStream* connection itself has that
constraint (see the Dockerfile comment on staying single-instance), and
OpenSky polling doesn't hold a persistent connection at all, just a
periodic REST GET, so it costs nothing extra to add the same host.

Deploy this as its own small service, separate from the main Vayu backend.
Required/optional env vars:
  AISSTREAM_API_KEY     your aisstream.io key
  OPENSKY_CLIENT_ID     your OpenSky OAuth2 client id (opensky-network.org
  OPENSKY_CLIENT_SECRET  -> Account -> API Clients) — falls back to
                         anonymous OpenSky access if unset, which works
                         but is unreliable even from a non-blocked IP
  BRIDGE_API_KEY         a secret you invent — the backend sends it back
                         as the X-Bridge-Key header on every request to
                         BOTH /vessels and /aircraft. Without this, anyone
                         who finds the bridge's public URL reads your feed
                         for free. Set this before deploying publicly.
"""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional

import httpx
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
logger = logging.getLogger("vayu_bridge")

AISSTREAM_API_KEY = os.environ.get("AISSTREAM_API_KEY", "")
BRIDGE_API_KEY = os.environ.get("BRIDGE_API_KEY", "")
AISSTREAM_WS_URL = "wss://stream.aisstream.io/v0/stream"

OPENSKY_CLIENT_ID = os.environ.get("OPENSKY_CLIENT_ID", "")
OPENSKY_CLIENT_SECRET = os.environ.get("OPENSKY_CLIENT_SECRET", "")
OPENSKY_URL = "https://opensky-network.org/api/states/all"
OPENSKY_TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
OPENSKY_POLL_INTERVAL_S = 90

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
IDLE_TIMEOUT_S = 180       # no application message in 3 min = feed considered stalled
HEARTBEAT_INTERVAL_S = 300 # log proof-of-life every 5 min


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

    def count(self) -> int:
        return len(self._vessels)


vessel_buffer = VesselBuffer()


# ── OpenSky aircraft tracking ────────────────────────────────────────────────
class AircraftBuffer:
    """Latest global OpenSky snapshot. Simple replace-on-poll, no
    per-aircraft merge needed — each poll is already a full state-vector
    snapshot, unlike AIS's per-message updates."""

    def __init__(self):
        self._aircraft: list[dict] = []
        self._lock = asyncio.Lock()
        self._last_error: Optional[str] = None
        self._last_success_at: Optional[str] = None

    async def load_snapshot(self, aircraft: list[dict]):
        async with self._lock:
            self._aircraft = aircraft

    def snapshot(self) -> list[dict]:
        return list(self._aircraft)

    def count(self) -> int:
        return len(self._aircraft)

    def record_error(self, error: str):
        self._last_error = error

    def record_success(self):
        self._last_error = None
        self._last_success_at = datetime.utcnow().isoformat() + "Z"

    def status(self) -> dict:
        return {"last_error": self._last_error, "last_success_at": self._last_success_at}


aircraft_buffer = AircraftBuffer()

# OpenSky state vector array indices (per their documented schema)
_OS_ICAO24, _OS_CALLSIGN, _OS_ORIGIN_COUNTRY = 0, 1, 2
_OS_LON, _OS_LAT, _OS_BARO_ALT = 5, 6, 7
_OS_ON_GROUND, _OS_VELOCITY, _OS_HEADING = 8, 9, 10
_OS_VERT_RATE = 11

_opensky_token_cache: dict = {}


async def _get_opensky_token(client: httpx.AsyncClient) -> Optional[str]:
    global _opensky_token_cache
    now = datetime.utcnow()
    cached = _opensky_token_cache.get("access_token")
    expires_at = _opensky_token_cache.get("expires_at")
    if cached and expires_at and now < expires_at:
        return cached

    try:
        resp = await client.post(
            OPENSKY_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": OPENSKY_CLIENT_ID,
                "client_secret": OPENSKY_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("access_token")
        expires_in = data.get("expires_in", 1800)
        if token:
            _opensky_token_cache = {
                "access_token": token,
                "expires_at": now + timedelta(seconds=expires_in - 60),
            }
        return token
    except Exception as e:
        logger.error(f"OpenSky OAuth error: {type(e).__name__}: {e}")
        return None


async def _fetch_opensky_snapshot() -> list[dict]:
    headers = {"User-Agent": "VAYU-Network-Bridge/1.0"}
    async with httpx.AsyncClient(headers=headers) as client:
        if OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET:
            token = await _get_opensky_token(client)
            if token:
                client.headers["Authorization"] = f"Bearer {token}"

        resp = await client.get(OPENSKY_URL, timeout=httpx.Timeout(20, connect=8))
        resp.raise_for_status()
        data = resp.json()

    states = data.get("states") or []
    aircraft = []
    for s in states:
        try:
            lat, lon = s[_OS_LAT], s[_OS_LON]
            if lat is None or lon is None:
                continue
            icao24 = s[_OS_ICAO24]
            if not icao24:
                continue
            callsign = (s[_OS_CALLSIGN] or "").strip()
            aircraft.append({
                "icao24": icao24,
                "callsign": callsign or icao24.upper(),
                "origin_country": s[_OS_ORIGIN_COUNTRY],
                "lat": lat,
                "lon": lon,
                "baro_altitude_m": s[_OS_BARO_ALT],
                "on_ground": bool(s[_OS_ON_GROUND]),
                "velocity_ms": s[_OS_VELOCITY],
                "heading": s[_OS_HEADING],
                "vertical_rate_ms": s[_OS_VERT_RATE],
                "last_update": datetime.utcnow().isoformat() + "Z",
            })
        except (IndexError, TypeError):
            continue
    return aircraft


async def _poll_opensky_forever():
    if not OPENSKY_CLIENT_ID:
        logger.info(
            "OPENSKY_CLIENT_ID not set — polling anonymously (works, but unreliable "
            "even from a non-blocked IP; register free at opensky-network.org)"
        )
    while True:
        try:
            aircraft = await _fetch_opensky_snapshot()
            await aircraft_buffer.load_snapshot(aircraft)
            aircraft_buffer.record_success()
            logger.debug(f"OpenSky poll: {len(aircraft)} aircraft")
        except Exception as e:
            logger.error(f"OpenSky poll error: {type(e).__name__}: {e}")
            aircraft_buffer.record_error(f"{type(e).__name__}: {e}".rstrip(": "))
        await asyncio.sleep(OPENSKY_POLL_INTERVAL_S)


class AISBridgeClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_message_at = datetime.utcnow()

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
            self._last_message_at = datetime.utcnow()  # fresh clock for this connection

            message_count = 0
            watchdog_task = asyncio.create_task(self._idle_watchdog(ws))
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(lambda: message_count))
            try:
                async for raw_message in ws:
                    if not self._running:
                        break
                    self._last_message_at = datetime.utcnow()
                    message_count += 1
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
            finally:
                watchdog_task.cancel()
                heartbeat_task.cancel()
                logger.info(f"AISStream: stream loop ended, received {message_count} messages this connection")

    async def _idle_watchdog(self, ws):
        """AISStream can leave the WebSocket technically alive (still
        answering pings) while silently stopping the actual data stream —
        this happened after ~3-4 days of otherwise-normal operation, with
        no error and no log line to show for it, since nothing at the
        transport level ever failed. ping/pong keepalive can't catch this
        because the server keeps responding to pings; only the absence of
        *application* messages reveals it. If no message has arrived in
        IDLE_TIMEOUT_S, force-close the socket so _run_forever's normal
        exception handling reconnects — the chokepoints here see enough
        real traffic that a multi-minute silence is never legitimate."""
        while True:
            await asyncio.sleep(15)
            idle_for = (datetime.utcnow() - self._last_message_at).total_seconds()
            if idle_for > IDLE_TIMEOUT_S:
                logger.warning(
                    f"AISStream: no messages received for {idle_for:.0f}s (feed appears stalled "
                    f"despite the connection being technically open) — forcing reconnect"
                )
                await ws.close()
                return

    async def _heartbeat_loop(self, get_count):
        """Periodic proof-of-life in the logs — so a future stall like this
        one is visible in the log timeline instead of just going silent."""
        last_count = 0
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            count = get_count()
            logger.info(f"AISStream: heartbeat — {count - last_count} messages in the last {HEARTBEAT_INTERVAL_S}s, {vessel_buffer.count()} vessels tracked")
            last_count = count


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
            "BRIDGE_API_KEY not set — /vessels and /aircraft are UNPROTECTED. "
            "Set it before deploying this publicly."
        )
    await ais_client.start()
    opensky_task = asyncio.create_task(_poll_opensky_forever(), name="opensky-poll")
    prune_task = asyncio.create_task(_prune_loop())
    yield
    prune_task.cancel()
    opensky_task.cancel()
    await ais_client.stop()


app = FastAPI(title="Vayu Network Bridge", version="1.1.0", lifespan=lifespan)


def _check_auth(x_bridge_key: Optional[str]):
    if BRIDGE_API_KEY and x_bridge_key != BRIDGE_API_KEY:
        raise HTTPException(status_code=401, detail="missing or invalid X-Bridge-Key header")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "vessel_count": len(vessel_buffer.snapshot()),
        "aircraft_count": aircraft_buffer.count(),
    }


@app.get("/vessels")
async def get_vessels(x_bridge_key: Optional[str] = Header(default=None)):
    _check_auth(x_bridge_key)
    vessels = vessel_buffer.snapshot()
    return {
        "vessels": vessels,
        "count": len(vessels),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


@app.get("/aircraft")
async def get_aircraft(x_bridge_key: Optional[str] = Header(default=None)):
    _check_auth(x_bridge_key)
    aircraft = aircraft_buffer.snapshot()
    status = aircraft_buffer.status()
    return {
        "aircraft": aircraft,
        "count": len(aircraft),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        **status,
    }
