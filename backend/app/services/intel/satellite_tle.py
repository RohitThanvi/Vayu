"""
satellite_tle.py — caches orbital element sets (TLEs) for live satellite
tracking, free and keyless.

Unlike aircraft/vessel positions (which the server holds current state
for), satellite position is a closed-form function of its TLE + time —
SGP4 propagation is cheap and better done client-side (via satellite.js
in the frontend) than recomputed here for every connected client. This
module's only job is caching the raw TLE catalog so the frontend has
orbital elements to propagate from.

Fetching moved to the ais-bridge service (Render Ohio) as of the same fix
already applied to AIS and aircraft: CelesTrak ConnectTimeouts from this
backend's own Oregon region directly (confirmed via this backend's own
logs), so refresh() now polls the bridge's /satellites/tle instead of
CelesTrak directly. See ais-bridge/app.py for the actual CelesTrak fetch
logic — deliberately duplicated there rather than imported, since that
service has zero import dependency on this backend by design.

Deliberately scoped to two curated CelesTrak groups rather than the full
~9,000-object active catalog:
  - "stations"  (~20 objects: ISS, Tiangong, etc.) — always relevant
  - "visual"    (~200 brightest objects, mag <4.5) — CelesTrak's own
    curated "worth tracking" list, matching the "God's Eye View"-style
    live sky view without asking a browser to SGP4-propagate thousands
    of objects every animation frame.

CelesTrak's own guidance is TLEs shouldn't be reused past ~1-2 days for
precision, but for a live-map *display* layer (not precision pointing),
this refreshes well within that window with margin to spare.
"""

import logging
import threading
import time
from typing import Any, Dict, List

import httpx

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 6 * 60 * 60   # 6h — comfortably within CelesTrak's ~1-2 day precision window

_cache: Dict[str, Any] = {"satellites": [], "cached_at": 0.0}
_lock = threading.Lock()


async def refresh_from_bridge(bridge_url: str, bridge_api_key: str = "") -> int:
    """Poll ais-bridge's /satellites/tle and refresh the local cache.
    Returns the satellite count. See module docstring for why this goes
    through the bridge instead of CelesTrak directly."""
    headers = {"X-Bridge-Key": bridge_api_key} if bridge_api_key else {}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{bridge_url.rstrip('/')}/satellites/tle", headers=headers)
        resp.raise_for_status()
        data = resp.json()

    satellites = data.get("satellites", [])
    with _lock:
        _cache["satellites"] = satellites
        _cache["cached_at"] = time.time()

    if data.get("last_error"):
        logger.warning(f"bridge->CelesTrak: {data['last_error']}")
    logger.info(f"TLE poll (via bridge): {len(satellites)} satellites cached")
    return len(satellites)


def get_satellites() -> Dict[str, Any]:
    """Return the cached TLE catalog. Empty list before the first refresh
    completes (a few seconds after startup) — the frontend treats that as
    'not loaded yet' rather than an error."""
    with _lock:
        return {
            "count": len(_cache["satellites"]),
            "satellites": list(_cache["satellites"]),
            "cached_at": _cache["cached_at"],
        }
