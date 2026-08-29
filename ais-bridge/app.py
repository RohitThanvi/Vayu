"""
Vayu Network Bridge (AIS + adsb.lol aircraft + CelesTrak satellites)
=======================================================================
A small, standalone service whose job is to hold outbound connections that
Render's datacenter IP range gets blocked on, and re-expose them as plain
REST endpoints the main Vayu backend polls over normal HTTP.

This bridge is a second Render web service, deployed in the Ohio region
(the main Vayu backend runs in Oregon) — NOT Fly.io. Three feeds live here:

1. AIS (AISStream.io) — WebSocket-only, one live connection per API key.
   When the main backend held that connection directly on Render Oregon,
   every attempt got rejected with HTTP 429 at the handshake — before
   AISStream even reads the API key — and regenerating the key didn't
   help. Moving the connection to this Render-Ohio service fixed it, which
   pointed at Render Oregon's specific shared outbound IP pool being
   blocked, not anything about the key or the code.

2. Aircraft — originally OpenSky, but OpenSky ConnectTimeouts from
   Render-Ohio too (confirmed via this service's own logs — even the OAuth
   token request itself never completes), unlike AIS. So unlike AIS,
   region-hopping within Render didn't fix OpenSky specifically — it
   appears to disfavor Render broadly, not just the Oregon range. Switched
   to adsb.lol instead: a free, keyless, community ADS-B aggregation API
   that doesn't have this problem. Its tradeoff is the opposite of
   OpenSky's — no single global-snapshot endpoint, only bounded
   point/radius queries (max 250nm) — so this polls a curated list of
   aviation-dense regions across every continent (see ADSBLOL_REGIONS)
   and merges the results, rather than one call covering the whole globe.

3. Satellites (CelesTrak) — same fix, same reason: CelesTrak also
   ConnectTimeouts from the main Oregon backend directly, confirmed via
   that backend's own logs. Fetched here and re-exposed via /satellites/tle
   instead, exactly like /vessels and /aircraft.

Both feeds run in the same lightweight service since neither needs its
own dedicated worker the way AIS's one-connection-per-key constraint
might suggest — only the *AISStream* connection itself has that
constraint (see the Dockerfile comment on staying single-instance), and
polling adsb.lol doesn't hold a persistent connection at all, just a
batch of periodic REST GETs, so it costs nothing extra to add here.

Deploy this as its own small service, separate from the main Vayu backend.
Required/optional env vars:
  AISSTREAM_API_KEY   your aisstream.io key
  BRIDGE_API_KEY       a secret you invent — the backend sends it back
                        as the X-Bridge-Key header on every request to
                        BOTH /vessels and /aircraft. Without this, anyone
                        who finds the bridge's public URL reads your feed
                        for free. Set this before deploying publicly.
adsb.lol needs no key at all — it's fully anonymous/keyless by design.
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
                # (Render injects real env vars directly, .env is purely a
                # local-dev convenience and is gitignored)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("vayu_bridge")

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


# ── Aircraft tracking (adsb.lol) ─────────────────────────────────────────────
class AircraftBuffer:
    """Latest merged adsb.lol snapshot across all regions. Simple
    replace-on-poll like AircraftBuffer always was — each poll cycle
    re-fetches and re-merges all regions into one fresh list, no
    per-aircraft incremental merge needed."""

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


# ── Satellite TLE tracking (CelesTrak) ───────────────────────────────────────
# Same fix as AIS and aircraft: CelesTrak also ConnectTimeouts from the main
# Oregon backend directly (confirmed via that backend's own logs), so it's
# fetched here instead and re-exposed, exactly like /vessels and /aircraft.
CELESTRAK_GROUPS = ["stations", "visual"]
CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle"
CELESTRAK_POLL_INTERVAL_S = 6 * 60 * 60   # TLEs are near-static within this window


class TLEBuffer:
    def __init__(self):
        self._satellites: list[dict] = []
        self._last_error: Optional[str] = None
        self._last_success_at: Optional[str] = None

    def load(self, satellites: list[dict]):
        self._satellites = satellites

    def snapshot(self) -> list[dict]:
        return list(self._satellites)

    def count(self) -> int:
        return len(self._satellites)

    def record_error(self, error: str):
        self._last_error = error

    def record_success(self):
        self._last_error = None
        self._last_success_at = datetime.utcnow().isoformat() + "Z"

    def status(self) -> dict:
        return {"last_error": self._last_error, "last_success_at": self._last_success_at}


tle_buffer = TLEBuffer()


def _parse_tle_text(text: str, group: str) -> list[dict]:
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    sats = []
    for i in range(0, len(lines) - 2, 3):
        name, line1, line2 = lines[i], lines[i + 1], lines[i + 2]
        if line1.startswith("1 ") and line2.startswith("2 "):
            sats.append({"name": name.strip(), "line1": line1, "line2": line2, "group": group})
    return sats


async def _fetch_tle_group(client: httpx.AsyncClient, group: str) -> list[dict]:
    url = CELESTRAK_URL.format(group=group)
    try:
        resp = await client.get(url, timeout=httpx.Timeout(20, connect=8))
        resp.raise_for_status()
        return _parse_tle_text(resp.text, group)
    except Exception as e:
        logger.warning(f"CelesTrak fetch error for group '{group}': {type(e).__name__}: {e}")
        raise


async def _poll_celestrak_forever():
    await asyncio.sleep(10)
    while True:
        try:
            headers = {"User-Agent": "VAYU-Network-Bridge/1.0"}
            async with httpx.AsyncClient(headers=headers) as client:
                all_sats: list[dict] = []
                seen = set()
                any_failed = False
                for group in CELESTRAK_GROUPS:
                    try:
                        sats = await _fetch_tle_group(client, group)
                    except Exception:
                        any_failed = True
                        continue
                    for s in sats:
                        if s["name"] in seen:
                            continue
                        seen.add(s["name"])
                        all_sats.append(s)
            tle_buffer.load(all_sats)
            if any_failed and not all_sats:
                tle_buffer.record_error("all CelesTrak groups failed this cycle")
            else:
                tle_buffer.record_success()
            logger.info(f"CelesTrak poll: {len(all_sats)} satellites cached")
        except Exception as e:
            logger.error(f"CelesTrak poll error: {type(e).__name__}: {e}")
            tle_buffer.record_error(f"{type(e).__name__}: {e}".rstrip(": "))
        await asyncio.sleep(CELESTRAK_POLL_INTERVAL_S)

ADSBLOL_POINT_URL = "https://api.adsb.lol/v2/point/{lat}/{lon}/{radius_nm}"
ADSBLOL_RADIUS_NM = 250          # adsb.lol's documented max per point query
ADSBLOL_POLL_INTERVAL_S = 180    # was 90s under the old concurrent-burst approach; the
                                  # sequential fetch below takes ~80s on its own now, so
                                  # this needs real headroom rather than overlapping polls
ADSBLOL_REQUEST_DELAY_S = 1.4    # spacing between sequential region requests — see
                                  # _fetch_adsblol_snapshot for why this replaced concurrency

# Curated aviation-dense region centers across every populated continent —
# not literal wall-to-wall global tiling (that would mean many hundreds of
# 250nm circles just to cover open ocean with no traffic, and would be a
# genuinely abusive request volume against a free, donation-funded,
# keyless community API). Each point's 250nm circle typically covers an
# entire metro area's air traffic and a good chunk of the surrounding
# region, so ~50 well-chosen points give broad worldwide coverage of where
# aircraft actually are, without hammering the service. Adjust freely.
#
# CONFIRMED IN PRODUCTION: firing these concurrently (even capped at 8 at
# once) tripped adsb.lol's rate limiter globally within the same poll
# cycle — every region started coming back 420/429 together, not just the
# ones near the concurrency cap. Fetching sequentially with real spacing
# (see ADSBLOL_REQUEST_DELAY_S) fixed it. If you're tempted to re-add
# concurrency for speed, don't — this was a real production failure, not
# a theoretical one.
ADSBLOL_REGIONS = [
    # North America
    ("Los Angeles", 34.05, -118.24), ("Dallas", 32.78, -96.80),
    ("Chicago", 41.88, -87.63), ("New York", 40.71, -74.01),
    ("Atlanta", 33.75, -84.39), ("Denver", 39.74, -104.99),
    ("Seattle", 47.61, -122.33), ("Toronto", 43.65, -79.38),
    ("Mexico City", 19.43, -99.13), ("Miami", 25.76, -80.19),
    # South America
    ("Sao Paulo", -23.55, -46.63), ("Buenos Aires", -34.60, -58.38),
    ("Bogota", 4.71, -74.07), ("Lima", -12.05, -77.04),
    ("Santiago", -33.45, -70.67),
    # Europe
    ("London", 51.51, -0.13), ("Paris", 48.85, 2.35),
    ("Frankfurt", 50.11, 8.68), ("Madrid", 40.42, -3.70),
    ("Rome", 41.90, 12.50), ("Amsterdam", 52.37, 4.90),
    ("Moscow", 55.76, 37.62), ("Istanbul", 41.01, 28.98),
    ("Warsaw", 52.23, 21.01),
    # Africa
    ("Cairo", 30.04, 31.24), ("Lagos", 6.52, 3.38),
    ("Johannesburg", -26.20, 28.05), ("Nairobi", -1.29, 36.82),
    ("Casablanca", 33.57, -7.59), ("Addis Ababa", 9.03, 38.74),
    # Middle East
    ("Dubai", 25.20, 55.27), ("Riyadh", 24.71, 46.68),
    ("Doha", 25.29, 51.53), ("Tel Aviv", 32.08, 34.78),
    # South Asia
    ("Delhi", 28.61, 77.21), ("Mumbai", 19.08, 72.88),
    ("Bengaluru", 12.97, 77.59), ("Karachi", 24.86, 67.01),
    ("Dhaka", 23.81, 90.41), ("Colombo", 6.93, 79.85),
    # East Asia
    ("Beijing", 39.90, 116.41), ("Shanghai", 31.23, 121.47),
    ("Tokyo", 35.68, 139.65), ("Seoul", 37.57, 126.98),
    ("Hong Kong", 22.32, 114.17), ("Taipei", 25.03, 121.57),
    # Southeast Asia
    ("Singapore", 1.35, 103.82), ("Bangkok", 13.76, 100.50),
    ("Jakarta", -6.21, 106.85), ("Manila", 14.60, 120.98),
    ("Kuala Lumpur", 3.14, 101.69), ("Ho Chi Minh City", 10.82, 106.63),
    # Oceania
    ("Sydney", -33.87, 151.21), ("Auckland", -36.85, 174.76),
    ("Perth", -31.95, 115.86),
]


def _adsblol_map_aircraft(ac: dict) -> Optional[dict]:
    """Map one adsb.lol 'ac' array entry (ADS-B Exchange v2-compatible
    schema) into the flat aircraft dict shape the main backend's
    AircraftStore already expects (see aircraft_store.py there).

    Deliberately captures most of what adsb.lol actually provides, not
    just the OpenSky-equivalent subset the original mapper had — adsb.lol
    gives real airspeed data (IAS/TAS/Mach), geometric (GPS) altitude
    alongside barometric, autopilot targets, signal quality, and finer
    dbFlags categories (military/PIA/LADD are each distinct bits, not
    just one generic "interesting" flag) that OpenSky never exposed."""
    hex_ = ac.get("hex")
    lat, lon = ac.get("lat"), ac.get("lon")
    if not hex_ or lat is None or lon is None:
        return None

    # adsb.lol/ADSBx schema quirk: alt_baro is either a number (feet) OR
    # the literal string "ground" when the aircraft is on the ground —
    # not a separate boolean flag the way OpenSky had on_ground.
    alt_baro = ac.get("alt_baro")
    on_ground = alt_baro == "ground"
    altitude_m = 0.0 if on_ground else (
        alt_baro * 0.3048 if isinstance(alt_baro, (int, float)) else None
    )
    alt_geom = ac.get("alt_geom")   # GPS/geometric altitude, ft — distinct from barometric

    def _num(key, factor=1.0):
        v = ac.get(key)
        return v * factor if isinstance(v, (int, float)) else None

    gs = ac.get("gs")               # ground speed, knots
    baro_rate = ac.get("baro_rate") # vertical rate, ft/min
    # dbFlags bit meanings per ADSBExchange/adsb.lol convention: 1=military,
    # 2=interesting, 4=PIA (privacy ICAO address), 8=LADD (limited disclosure)
    db_flags = ac.get("dbFlags", 0) or 0

    return {
        "icao24": hex_,
        "callsign": (ac.get("flight") or hex_).strip() or hex_.upper(),
        "registration": ac.get("r"),
        "type_code": ac.get("t"),
        "type_desc": ac.get("desc"),
        "origin_country": None,   # adsb.lol doesn't provide this the way OpenSky did
        "lat": lat,
        "lon": lon,
        "baro_altitude_m": altitude_m,
        "geom_altitude_m": _num("alt_geom", 0.3048) if not on_ground else 0.0,
        "on_ground": on_ground,
        "velocity_ms": (gs * 0.514444) if isinstance(gs, (int, float)) else None,
        "ias_ms": _num("ias", 0.514444),           # indicated airspeed
        "tas_ms": _num("tas", 0.514444),           # true airspeed
        "mach": ac.get("mach"),
        "heading": ac.get("track"),
        "track_rate": ac.get("track_rate"),
        "roll": ac.get("roll"),
        "nav_heading": ac.get("nav_heading"),      # autopilot-selected heading
        "nav_altitude_mcp_m": _num("nav_altitude_mcp", 0.3048),  # autopilot-selected altitude
        "vertical_rate_ms": (baro_rate * 0.00508) if isinstance(baro_rate, (int, float)) else None,
        "squawk": ac.get("squawk"),
        "category": ac.get("category"),
        "emergency": ac.get("emergency"),
        "military": bool(db_flags & 1),
        "interesting": bool(db_flags & 2),
        "pia": bool(db_flags & 4),                 # privacy ICAO address program
        "ladd": bool(db_flags & 8),                # limited aircraft data disclosure
        "nic": ac.get("nic"),                      # navigation integrity category
        "rssi": ac.get("rssi"),                    # signal strength — rough proxy for receiver distance
        "messages": ac.get("messages"),            # total ADS-B messages seen from this aircraft
        "seen_s": ac.get("seen"),                  # seconds since last message of any kind
        "seen_pos_s": ac.get("seen_pos"),          # seconds since last position update
        "last_update": datetime.utcnow().isoformat() + "Z",
    }


async def _fetch_adsblol_region(client: httpx.AsyncClient, name: str, lat: float, lon: float) -> Optional[list[dict]]:
    """Returns None on a connection/HTTP failure (distinct from a
    legitimate empty [] result, which just means no aircraft in range
    right now) — the caller uses that distinction to run a circuit
    breaker on consecutive real failures."""
    url = ADSBLOL_POINT_URL.format(lat=lat, lon=lon, radius_nm=ADSBLOL_RADIUS_NM)
    for attempt in (1, 2):
        try:
            resp = await client.get(url, timeout=httpx.Timeout(15, connect=8))
            if resp.status_code in (420, 429):
                # Rate-limited. Honor Retry-After if adsb.lol sends one,
                # otherwise back off a fixed amount — one retry only, this
                # is a per-region fetch inside a larger sequential poll, not
                # worth hammering further if it's still limited afterward.
                if attempt == 1:
                    retry_after = resp.headers.get("Retry-After")
                    wait_s = float(retry_after) if retry_after and retry_after.isdigit() else 5.0
                    logger.warning(f"adsb.lol region '{name}' rate-limited ({resp.status_code}), retrying in {wait_s:.0f}s")
                    await asyncio.sleep(wait_s)
                    continue
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception as e:
            logger.warning(f"adsb.lol region '{name}' fetch failed: {type(e).__name__}: {e}")
            return None
    else:
        return None
    out = []
    for ac in (data.get("ac") or []):
        mapped = _adsblol_map_aircraft(ac)
        if mapped:
            out.append(mapped)
    return out


ADSBLOL_CIRCUIT_BREAKER_THRESHOLD = 6   # consecutive real failures before aborting the rest of this cycle


async def _fetch_adsblol_snapshot() -> list[dict]:
    # Sequential with real spacing between requests, not concurrent bursts.
    # The original concurrency-8 burst approach tripped adsb.lol's rate
    # limiter globally (420/429 on nearly every region, every cycle) — this
    # is a free community API, not a CDN built for parallel hammering. 55
    # regions * ~1.4s apart is ~80s, comfortably inside the poll interval.
    #
    # Circuit breaker: seen in production, a batch of consecutive
    # ConnectTimeouts (not 420/429 — a harsher connection-level failure,
    # possibly a temporary block after the earlier burst-abuse period)
    # meant EVERY remaining region failed the same way, one by one, each
    # eating a full ~8s connect timeout — turning a single poll cycle into
    # 7+ minutes of guaranteed-doomed retries. If several regions in a row
    # fail for real (None, not just an empty-but-successful result), stop
    # for this cycle and let the next one (in ADSBLOL_POLL_INTERVAL_S) try
    # fresh rather than grinding through the rest for no benefit.
    headers = {"User-Agent": "VAYU-Network-Bridge/1.0"}
    all_results = []
    consecutive_failures = 0
    async with httpx.AsyncClient(headers=headers) as client:
        for name, lat, lon in ADSBLOL_REGIONS:
            result = await _fetch_adsblol_region(client, name, lat, lon)
            if result is None:
                consecutive_failures += 1
                if consecutive_failures >= ADSBLOL_CIRCUIT_BREAKER_THRESHOLD:
                    logger.error(
                        f"adsb.lol: {consecutive_failures} consecutive region failures, "
                        f"aborting rest of this cycle early (tried {all_results.__len__() + consecutive_failures}/{len(ADSBLOL_REGIONS)} regions)"
                    )
                    break
            else:
                consecutive_failures = 0
                all_results.append(result)
            await asyncio.sleep(ADSBLOL_REQUEST_DELAY_S)

    # Regions' circles overlap at the edges (adjacent metro areas within
    # 250nm of each other) — dedupe by icao24 hex, last region wins for
    # any given aircraft (arbitrary but harmless, positions agree within
    # a few seconds of each other regardless of which region reported it).
    merged: dict[str, dict] = {}
    for region_result in all_results:
        for ac in region_result:
            merged[ac["icao24"]] = ac
    return list(merged.values())


async def _poll_adsblol_forever():
    while True:
        try:
            aircraft = await _fetch_adsblol_snapshot()
            await aircraft_buffer.load_snapshot(aircraft)
            aircraft_buffer.record_success()
            logger.debug(f"adsb.lol poll: {len(aircraft)} aircraft across {len(ADSBLOL_REGIONS)} regions")
        except Exception as e:
            logger.error(f"adsb.lol poll error: {type(e).__name__}: {e}")
            aircraft_buffer.record_error(f"{type(e).__name__}: {e}".rstrip(": "))
        await asyncio.sleep(ADSBLOL_POLL_INTERVAL_S)



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
    adsblol_task = asyncio.create_task(_poll_adsblol_forever(), name="adsblol-poll")
    celestrak_task = asyncio.create_task(_poll_celestrak_forever(), name="celestrak-poll")
    prune_task = asyncio.create_task(_prune_loop())
    yield
    prune_task.cancel()
    adsblol_task.cancel()
    celestrak_task.cancel()
    await ais_client.stop()


app = FastAPI(title="Vayu Network Bridge", version="1.3.0", lifespan=lifespan)


def _check_auth(x_bridge_key: Optional[str]):
    if BRIDGE_API_KEY and x_bridge_key != BRIDGE_API_KEY:
        raise HTTPException(status_code=401, detail="missing or invalid X-Bridge-Key header")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "vessel_count": len(vessel_buffer.snapshot()),
        "aircraft_count": aircraft_buffer.count(),
        "satellite_count": tle_buffer.count(),
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


@app.get("/satellites/tle")
async def get_satellite_tles(x_bridge_key: Optional[str] = Header(default=None)):
    _check_auth(x_bridge_key)
    satellites = tle_buffer.snapshot()
    status = tle_buffer.status()
    return {
        "satellites": satellites,
        "count": len(satellites),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        **status,
    }
