"""
air_quality.py — real-time Air Quality Index (AQI) from CPCB (India's
Central Pollution Control Board), added as an intel layer per an
advisor's suggestion.

Source: data.gov.in's "Real time Air Quality Index" catalog
(resource id 3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69), which republishes
CPCB's own station network (~800+ monitoring stations across India,
hourly updates) as a proper JSON API. Needs a free API key from
data.gov.in (signup with email — no card required, same free-tier shape
as the other keyed-but-free sources already in this project). Set
AQI_API_KEY in the environment.

This is India-only, matching CPCB's actual coverage — not a global
layer like the other intel sources (USGS/FIRMS/GDELT/ACLED/AIS). That's
an honest scope limit of the underlying data, not a bug: Vayu's primary
use is India-centric geospatial work, so a India-only real-time AQI
layer is still a real addition even though it doesn't cover the whole
globe the way the other intel sources do.

Response shape from the raw API is one row per (station, pollutant) —
e.g. a single station reporting PM2.5, PM10, NO2, SO2, CO, O3 each
comes back as up to 6 separate rows sharing the same station id/name/
lat/lon. This module groups those rows back into one record per
station with a nested pollutants dict, and computes an overall station
AQI as the max of its reported sub-indices (this is the standard
"AQI = worst pollutant" convention CPCB's own National AQI uses, not
an average — averaging pollutant sub-indices would understate real
health risk from a single badly-elevated pollutant).
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60 * 60   # CPCB stations report hourly — matches their own update cadence

AQI_API_URL = "https://api.data.gov.in/resource/3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69"

# CPCB's own National AQI category bands (cpcb.nic.in/National-Air-Quality-Index),
# used consistently across the frontend legend and this module's category label.
AQI_BANDS = [
    (50,  "Good",        "#00b050"),
    (100, "Satisfactory", "#92d050"),
    (200, "Moderate",     "#ffff00"),
    (300, "Poor",         "#ff9900"),
    (400, "Very Poor",    "#ff0000"),
    (float("inf"), "Severe", "#800000"),
]


def _category_for(aqi: float) -> Dict[str, str]:
    for threshold, label, color in AQI_BANDS:
        if aqi <= threshold:
            return {"label": label, "color": color}
    return {"label": "Severe", "color": "#800000"}


_cache: Dict[str, Any] = {"stations": [], "cached_at": 0.0, "last_error": None}
_lock = threading.Lock()


def _parse_float(v) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f else None   # filter NaN
    except (TypeError, ValueError):
        return None


async def refresh(api_key: str, force: bool = False, limit: int = 3000) -> int:
    """Fetch the current CPCB AQI snapshot and refresh the cache. Returns
    the station count. A missing api_key or a failed request leaves the
    existing cache in place (stale-but-present beats empty) and records
    last_error, matching the failure-handling pattern used across this
    project's other intel sources."""
    if not api_key:
        with _lock:
            _cache["last_error"] = "AQI_API_KEY not configured"
        logger.warning("Air quality refresh skipped — AQI_API_KEY not configured")
        return len(_cache["stations"])

    if not force:
        with _lock:
            cached_at = _cache["cached_at"]
        if cached_at and (time.time() - cached_at) < CACHE_TTL_SECONDS:
            return len(_cache["stations"])

    params = {
        "api-key": api_key,
        "format": "json",
        "limit": limit,
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(AQI_API_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        with _lock:
            _cache["last_error"] = f"{type(e).__name__}: {e}"
        logger.warning(f"Air quality fetch failed: {type(e).__name__}: {e}")
        return len(_cache["stations"])

    records = data.get("records", [])
    if not records:
        with _lock:
            _cache["last_error"] = "CPCB API returned no records this cycle"
        logger.warning("Air quality: 0 records in CPCB response")
        return len(_cache["stations"])

    # Group per-pollutant rows back into one record per station.
    stations: Dict[str, Dict[str, Any]] = {}
    for row in records:
        station_id = row.get("station") or row.get("id")
        lat = _parse_float(row.get("latitude"))
        lon = _parse_float(row.get("longitude"))
        if not station_id or lat is None or lon is None:
            continue

        st = stations.setdefault(station_id, {
            "station_id": station_id,
            "station_name": row.get("station") or station_id,
            "city": row.get("city"),
            "state": row.get("state"),
            "lat": lat,
            "lon": lon,
            "last_update": row.get("last_update"),
            "pollutants": {},
        })

        pollutant_id = (row.get("pollutant_id") or "").upper()
        avg_value = _parse_float(row.get("pollutant_avg"))
        if pollutant_id and avg_value is not None:
            st["pollutants"][pollutant_id] = avg_value

    results = []
    for st in stations.values():
        if not st["pollutants"]:
            continue
        # CPCB's own convention: overall AQI = the worst individual
        # pollutant sub-index, not an average across pollutants.
        aqi = max(st["pollutants"].values())
        st["aqi"] = round(aqi, 1)
        st["category"] = _category_for(aqi)
        results.append(st)

    with _lock:
        if results:
            _cache["stations"] = results
            _cache["cached_at"] = time.time()
            _cache["last_error"] = None
        else:
            _cache["last_error"] = "CPCB response parsed but yielded no usable stations"

    logger.info(f"Air quality: {len(results)} stations refreshed")
    return len(results)


def get_stations() -> Dict[str, Any]:
    with _lock:
        return {
            "stations": list(_cache["stations"]),
            "cached_at": _cache["cached_at"],
            "last_error": _cache["last_error"],
        }
