"""
marine_data.py — one normalised hourly sea/weather forecast for a point.

Sources (free, keyless, no account): the Open-Meteo Marine API (waves, swell,
currents, sea-surface temperature, sea level incl. tide) and the Open-Meteo
Forecast API (wind, gusts, precipitation, WMO weather code, visibility, CAPE).

Licensing: Open-Meteo's free tier is for NON-COMMERCIAL use and requires
attribution (Open-Meteo, and DWD for the wave model data). A commercial/Business
deployment needs a paid Open-Meteo plan — set OPEN_METEO_API_KEY (sent as `apikey`)
and, if your plan uses different hosts, OPEN_METEO_MARINE_URL / OPEN_METEO_FORECAST_URL.

Robustness (this feeds a SAFETY screen, so silent gaps are not acceptable):
  * an invalid variable makes Open-Meteo reject the whole call (HTTP 400), so each
    request asks for core variables + optional extras and retries with the core set;
  * marine models only have values over open water at ~5 km. A point on/near land
    returns nulls, so we probe nearby cells and report exactly how far we moved;
  * every degradation is recorded in `notes` for the caller to show the user;
  * results are cached 30 min per (rounded) location.
"""

import asyncio
import logging
import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

MARINE_URL = os.environ.get("OPEN_METEO_MARINE_URL", "https://marine-api.open-meteo.com/v1/marine")
FORECAST_URL = os.environ.get("OPEN_METEO_FORECAST_URL", "https://api.open-meteo.com/v1/forecast")
API_KEY = os.environ.get("OPEN_METEO_API_KEY", "")

MARINE_CORE = ["wave_height", "wave_direction", "wave_period", "wind_wave_height",
               "swell_wave_height", "swell_wave_period"]
MARINE_EXTRA = ["ocean_current_velocity", "ocean_current_direction", "sea_surface_temperature", "sea_level_height_msl"]
WEATHER_CORE = ["wind_speed_10m", "wind_gusts_10m", "precipitation", "weather_code"]
WEATHER_EXTRA = ["visibility", "cape"]

CACHE_TTL_S = 30 * 60
HTTP_TIMEOUT_S = 15
SNAP_STEPS_DEG = (0.1, 0.2, 0.3)      # ~11 km, ~22 km, ~33 km
ATTRIBUTION = ("Weather and marine forecasts by Open-Meteo.com (wave model data: DWD and others). "
               "Free tier is licensed for non-commercial use.")

_cache: Dict[str, Tuple[float, Any]] = {}


def _cache_get(key: str):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
        return hit[1]
    _cache.pop(key, None)
    return None


def _cache_put(key: str, value: Any):
    if len(_cache) > 300:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.monotonic(), value)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def _get_json(url: str, params: Dict[str, Any]) -> Tuple[Optional[dict], Optional[str]]:
    """-> (json, error). One retry on transient failure; a 400 is returned
    immediately (it means 'bad variable', which retrying cannot fix)."""
    if API_KEY:
        params = {**params, "apikey": API_KEY}
    last = "unknown"
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
                resp = await client.get(url, params=params)
            if resp.status_code == 400:
                try:
                    reason = resp.json().get("reason", "bad request")
                except Exception:
                    reason = "bad request"
                return None, f"HTTP 400: {reason}"
            resp.raise_for_status()
            return resp.json(), None
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            await asyncio.sleep(0.5 * (attempt + 1))
    return None, last


async def _call(url: str, lat: float, lon: float, core: List[str], extra: List[str], days: int) -> Tuple[Optional[dict], Optional[str], bool]:
    """Request core+extra; on a 400 retry with core only. -> (json, error, extras_dropped)."""
    base = {"latitude": round(lat, 4), "longitude": round(lon, 4), "forecast_days": days, "timezone": "auto"}
    data, err = await _get_json(url, {**base, "hourly": ",".join(core + extra)})
    if data is not None:
        return data, None, False
    if err and err.startswith("HTTP 400") and extra:
        data, err2 = await _get_json(url, {**base, "hourly": ",".join(core)})
        if data is not None:
            return data, None, True
        return None, err2, False
    return None, err, False


def _series(block: dict, name: str, n: int) -> List[Optional[float]]:
    vals = (block or {}).get(name)
    if not isinstance(vals, list):
        return [None] * n
    out = [v if isinstance(v, (int, float)) else None for v in vals[:n]]
    return out + [None] * (n - len(out))


def _has_waves(js: Optional[dict]) -> bool:
    if not js:
        return False
    vals = (js.get("hourly") or {}).get("wave_height") or []
    return any(isinstance(v, (int, float)) for v in vals)


async def _marine_with_snap(lat: float, lon: float, days: int, notes: List[str]) -> Tuple[Optional[dict], float, float, float]:
    """-> (marine_json, used_lat, used_lon, snap_km)."""
    js, err, dropped = await _call(MARINE_URL, lat, lon, MARINE_CORE, MARINE_EXTRA, days)
    if err:
        notes.append(f"Marine forecast request failed ({err}).")
        return None, lat, lon, 0.0
    if dropped:
        notes.append("Some optional marine variables (currents, SST, tide) were unavailable.")
    if _has_waves(js):
        return js, lat, lon, 0.0

    # No wave data at the exact point: probe the 8 surrounding cells at growing radii.
    for step in SNAP_STEPS_DEG:
        offsets = [(step, 0), (-step, 0), (0, step), (0, -step), (step, step), (step, -step), (-step, step), (-step, -step)]
        results = await asyncio.gather(*[
            _call(MARINE_URL, lat + dy, lon + dx, MARINE_CORE, MARINE_EXTRA, days) for dy, dx in offsets
        ])
        valid = [((lat + dy, lon + dx), r[0]) for (dy, dx), r in zip(offsets, results) if r[0] is not None and _has_waves(r[0])]
        if valid:
            valid.sort(key=lambda v: haversine_km(lat, lon, v[0][0], v[0][1]))
            (ulat, ulon), best = valid[0]
            km = haversine_km(lat, lon, ulat, ulon)
            notes.append(f"No sea-model data exactly at this point (land or very close to shore); using the nearest sea cell {km:.0f} km away.")
            return best, ulat, ulon, km
    notes.append("No marine model data found within ~35 km of this point; it may be inland.")
    return None, lat, lon, 0.0


async def fetch_forecast(lat: float, lon: float, days: int = 3) -> Dict[str, Any]:
    """Normalised forecast. Never raises. `ok` is False when no usable data was found.

    Shape: {ok, lat, lon, used_lat, used_lon, snap_km, utc_offset_s, hours: [...], notes: [...],
            has_marine, has_weather, attribution}
    where each hour is {t, wave_h, wave_period, wave_dir, wind_wave_h, swell_h, swell_period,
    current_kmh, current_dir, sst_c, sea_level_m, wind_kmh, gust_kmh, precip_mm, wcode, vis_m, cape}.
    `t` is an ISO local-time string ("2026-10-14T06:00"), local to the location.
    """
    days = max(1, min(days, 5))
    key = f"{round(lat, 2)}|{round(lon, 2)}|{days}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    notes: List[str] = []
    marine_res, weather_res = await asyncio.gather(
        _marine_with_snap(lat, lon, days, notes),
        _call(FORECAST_URL, lat, lon, WEATHER_CORE, WEATHER_EXTRA, days),
    )
    marine, ulat, ulon, snap_km = marine_res
    weather, werr, wdropped = weather_res
    if werr:
        notes.append(f"Weather forecast request failed ({werr}).")
    elif wdropped:
        notes.append("Visibility/CAPE were unavailable, so those checks were skipped.")

    base = marine or weather
    if not base or not (base.get("hourly") or {}).get("time"):
        out = {"ok": False, "lat": lat, "lon": lon, "used_lat": ulat, "used_lon": ulon, "snap_km": snap_km,
               "utc_offset_s": 0, "hours": [], "notes": notes + ["No forecast data could be retrieved."],
               "has_marine": False, "has_weather": False, "attribution": ATTRIBUTION}
        return out          # not cached: a failure shouldn't stick for 30 minutes

    times = list(base["hourly"]["time"])
    n = len(times)
    mh = (marine or {}).get("hourly") or {}
    wh = (weather or {}).get("hourly") or {}
    w_times = wh.get("time") or []
    # Marine and weather share timezone=auto for nearby points, but align by timestamp to be safe.
    w_index = {t: i for i, t in enumerate(w_times)}

    def w(name: str) -> List[Optional[float]]:
        raw = _series(wh, name, len(w_times))
        return [raw[w_index[t]] if t in w_index else None for t in times]

    series = {
        "wave_h": _series(mh, "wave_height", n), "wave_period": _series(mh, "wave_period", n),
        "wave_dir": _series(mh, "wave_direction", n), "wind_wave_h": _series(mh, "wind_wave_height", n),
        "swell_h": _series(mh, "swell_wave_height", n), "swell_period": _series(mh, "swell_wave_period", n),
        "current_kmh": _series(mh, "ocean_current_velocity", n), "current_dir": _series(mh, "ocean_current_direction", n),
        "sst_c": _series(mh, "sea_surface_temperature", n), "sea_level_m": _series(mh, "sea_level_height_msl", n),
        "wind_kmh": w("wind_speed_10m"), "gust_kmh": w("wind_gusts_10m"), "precip_mm": w("precipitation"),
        "wcode": w("weather_code"), "vis_m": w("visibility"), "cape": w("cape"),
    }
    hours = [{"t": times[i], **{k: v[i] for k, v in series.items()}} for i in range(n)]
    out = {
        "ok": True, "lat": lat, "lon": lon, "used_lat": ulat, "used_lon": ulon, "snap_km": round(snap_km, 1),
        "utc_offset_s": int(base.get("utc_offset_seconds") or 0), "hours": hours, "notes": notes,
        "has_marine": bool(marine and _has_waves(marine)), "has_weather": bool(weather),
        "attribution": ATTRIBUTION,
    }
    _cache_put(key, out)
    return out
