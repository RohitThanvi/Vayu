"""
satellite_tle.py — fetches and caches orbital element sets (TLEs) from
CelesTrak for live satellite tracking, free and keyless.

Unlike aircraft/vessel positions (which the server holds current state
for), satellite position is a closed-form function of its TLE + time —
SGP4 propagation is cheap and better done client-side (via satellite.js
in the frontend) than recomputed here for every connected client. This
service's only job is fetching + caching the raw TLE catalog so the
frontend has orbital elements to propagate from.

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

CELESTRAK_GROUPS = ["stations", "visual"]
CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle"

_cache: Dict[str, Any] = {"satellites": [], "cached_at": 0.0}
_lock = threading.Lock()


def _parse_tle_text(text: str, group: str) -> List[Dict[str, str]]:
    """CelesTrak TLE-format text is 3 lines per object: name, line1, line2."""
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    sats = []
    for i in range(0, len(lines) - 2, 3):
        name, line1, line2 = lines[i], lines[i + 1], lines[i + 2]
        if line1.startswith("1 ") and line2.startswith("2 "):
            sats.append({
                "name": name.strip(),
                "line1": line1,
                "line2": line2,
                "group": group,
            })
    return sats


async def _fetch_group(client: httpx.AsyncClient, group: str) -> List[Dict[str, str]]:
    url = CELESTRAK_URL.format(group=group)
    try:
        resp = await client.get(url, timeout=20)
        resp.raise_for_status()
        return _parse_tle_text(resp.text, group)
    except Exception as e:
        logger.error(f"CelesTrak fetch error for group '{group}': {type(e).__name__}: {e}")
        return []


async def refresh() -> int:
    """Fetch all configured CelesTrak groups and refresh the cache.
    Returns the total satellite count. Called by the scheduler on an
    interval; also safe to call on-demand from get_satellites() below if
    the cache has expired."""
    async with httpx.AsyncClient(headers={"User-Agent": "VAYU-Intelligence-Terminal/2.0"}) as client:
        all_sats: List[Dict[str, str]] = []
        seen = set()
        for group in CELESTRAK_GROUPS:
            sats = await _fetch_group(client, group)
            for s in sats:
                # "stations" and "visual" can overlap (e.g. ISS is in both) —
                # dedupe by name, first group wins.
                if s["name"] in seen:
                    continue
                seen.add(s["name"])
                all_sats.append(s)

    with _lock:
        _cache["satellites"] = all_sats
        _cache["cached_at"] = time.time()

    logger.info(f"CelesTrak: cached {len(all_sats)} satellite TLEs")
    return len(all_sats)


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
