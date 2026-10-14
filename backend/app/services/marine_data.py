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
  * marine and weather halves are cached separately (30 min fresh); if a live call fails we fall
    back to a clearly-labelled copy up to 3 h old rather than leave a hole in a safety screen;
  * HTTP 429 / 5xx are retried with back-off (honouring Retry-After); errors are reported in plain
    words, never as raw URLs;
  * OPTIONAL last-resort wind/rain fallback from MET Norway (set MET_NO_USER_AGENT to a descriptive
    string with a contact address, as their terms require; check their licence for your use). For
    India MET Norway has mean wind but NO gust data, so gusts are only ESTIMATED from mean wind and
    sea_safety will never issue GO on estimated gusts.
"""

import asyncio
import datetime as dt
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
STALE_MAX_S = 3 * 3600
HTTP_TIMEOUT_S = 15
MAX_ATTEMPTS = 3
MAX_BACKOFF_S = 4.0
MET_URL = os.environ.get("MET_NO_URL", "https://api.met.no/weatherapi/locationforecast/2.0/compact")
MET_UA = os.environ.get("MET_NO_USER_AGENT", "")      # required by MET Norway's terms; fallback is off without it
GUST_FACTOR_ESTIMATE = 1.5                            # 3 s gust ~ 1.5 x 10 min mean over open sea (conservative screening value)
SNAP_STEPS_DEG = (0.1, 0.2, 0.3)      # ~11 km, ~22 km, ~33 km
ATTRIBUTION = ("Weather and marine forecasts by Open-Meteo.com (wave model data: DWD and others). "
               "Free tier is licensed for non-commercial use.")

_cache: Dict[str, Tuple[float, Any]] = {}


def _cache_get(key: str, max_age: float = CACHE_TTL_S) -> Tuple[Optional[Any], Optional[float]]:
    """-> (value, age_seconds) if an entry exists and is younger than max_age, else (None, None)."""
    hit = _cache.get(key)
    if hit:
        age = time.monotonic() - hit[0]
        if age < max_age:
            return hit[1], age
        if age >= STALE_MAX_S:
            _cache.pop(key, None)
    return None, None


def _cache_put(key: str, value: Any):
    if len(_cache) > 400:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.monotonic(), value)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _retry_after(resp: "httpx.Response") -> Optional[float]:
    try:
        return float(resp.headers.get("retry-after", ""))
    except ValueError:
        return None


async def _get_json(url: str, params: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Tuple[Optional[dict], Optional[str]]:
    """-> (json, error). Retries 429 / 5xx / timeouts with back-off (honouring Retry-After). A 400 is
    returned immediately (it means 'bad variable', which retrying cannot fix). Error strings are
    plain words — never URLs or exception reprs, which would leak query strings into the UI."""
    if API_KEY and "open-meteo" in url:
        params = {**params, "apikey": API_KEY}
    last = "unknown error"
    for attempt in range(MAX_ATTEMPTS):
        wait = 0.5 * (2 ** attempt)
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S, headers=headers or {}) as client:
                resp = await client.get(url, params=params)
            code = resp.status_code
            if code == 400:
                try:
                    reason = resp.json().get("reason", "bad request")
                except Exception:
                    reason = "bad request"
                return None, f"HTTP 400: {reason}"
            if code == 429:
                last = "rate-limited by the weather service (HTTP 429)"
                wait = _retry_after(resp) or wait * 2
            elif code >= 500:
                last = f"weather service error (HTTP {code})"
            elif code >= 400:
                return None, f"weather service refused the request (HTTP {code})"
            else:
                return resp.json(), None
        except httpx.TimeoutException:
            last = "the weather service timed out"
        except Exception as e:
            last = f"network error ({type(e).__name__})"
        if attempt < MAX_ATTEMPTS - 1:
            await asyncio.sleep(min(wait, MAX_BACKOFF_S))
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


async def _fetch_marine_cached(lat: float, lon: float, days: int, notes: List[str]) -> Tuple[Optional[dict], float, float, float]:
    key = f"M|{round(lat, 2)}|{round(lon, 2)}|{days}"
    fresh, _ = _cache_get(key)
    if fresh is not None:
        result, local_notes = fresh
        notes.extend(local_notes)
        return result
    local: List[str] = []
    result = await _marine_with_snap(lat, lon, days, local)
    if result[0] is not None:
        _cache_put(key, (result, local))
        notes.extend(local)
        return result
    stale, age = _cache_get(key, STALE_MAX_S)
    if stale is not None:
        res, old_notes = stale
        notes.extend(old_notes)
        notes.append(f"Live marine forecast unavailable; using the copy fetched {int(age // 60)} min ago.")
        return res
    notes.extend(local)
    return result


async def _met_weather(lat: float, lon: float, utc_offset_s: int) -> Optional[dict]:
    """Optional last-resort wind/rain from MET Norway, reshaped like an Open-Meteo hourly block.
    No gust / visibility / CAPE for the global model; thunder is read from the symbol code."""
    if not MET_UA:
        return None
    data, err = await _get_json(MET_URL, {"lat": round(lat, 4), "lon": round(lon, 4)}, headers={"User-Agent": MET_UA})
    if not data:
        logger.warning(f"MET Norway fallback failed: {err}")
        return None
    times, wind, precip, wcode = [], [], [], []
    for entry in (data.get("properties") or {}).get("timeseries", []):
        n1 = (entry.get("data") or {}).get("next_1_hours")
        details = ((entry.get("data") or {}).get("instant") or {}).get("details") or {}
        if not n1 or details.get("wind_speed") is None:
            continue                                            # keep hourly steps only
        try:
            t_utc = dt.datetime.fromisoformat(entry["time"].replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            continue
        t_local = t_utc + dt.timedelta(seconds=utc_offset_s)
        symbol = ((n1.get("summary") or {}).get("symbol_code") or "")
        times.append(t_local.replace(minute=0, second=0, microsecond=0).isoformat(timespec="minutes"))
        wind.append(round(float(details["wind_speed"]) * 3.6, 1))          # m/s -> km/h
        precip.append((n1.get("details") or {}).get("precipitation_amount"))
        wcode.append(95 if "thunder" in symbol else 1)
    if not times:
        return None
    return {"utc_offset_seconds": utc_offset_s, "hourly": {"time": times, "wind_speed_10m": wind,
            "wind_gusts_10m": [None] * len(times), "precipitation": precip, "weather_code": wcode},
            "_source": "met.no"}


async def _fetch_weather_cached(lat: float, lon: float, days: int, utc_offset_s: int, notes: List[str]) -> Optional[dict]:
    key = f"W|{round(lat, 2)}|{round(lon, 2)}|{days}"
    fresh, _ = _cache_get(key)
    if fresh is not None:
        weather, local_notes = fresh
        notes.extend(local_notes)
        return weather
    data, err, dropped = await _call(FORECAST_URL, lat, lon, WEATHER_CORE, WEATHER_EXTRA, days)
    local: List[str] = []
    if data is not None:
        if dropped:
            local.append("Visibility/CAPE were unavailable, so those checks were skipped.")
        _cache_put(key, (data, local))
        notes.extend(local)
        return data
    stale, age = _cache_get(key, STALE_MAX_S)
    if stale is not None:
        weather, old_notes = stale
        notes.extend(old_notes)
        notes.append(f"Live weather forecast unavailable ({err}); using the copy fetched {int(age // 60)} min ago.")
        return weather
    met = await _met_weather(lat, lon, utc_offset_s)
    if met is not None:
        notes.append(f"Open-Meteo weather unavailable ({err}). Wind and rain come from MET Norway instead; it has no gust data, "
                     f"so gusts are ESTIMATED from mean wind and a GO verdict is not issued.")
        return met
    notes.append(f"Weather forecast unavailable: {err}.")
    return None


async def fetch_forecast(lat: float, lon: float, days: int = 3) -> Dict[str, Any]:
    """Normalised forecast. Never raises. `ok` is False when no usable data was found.

    Shape: {ok, lat, lon, used_lat, used_lon, snap_km, utc_offset_s, hours: [...], notes: [...],
            has_marine, has_weather, attribution}
    where each hour is {t, wave_h, wave_period, wave_dir, wind_wave_h, swell_h, swell_period,
    current_kmh, current_dir, sst_c, sea_level_m, wind_kmh, gust_kmh, gust_est_kmh, precip_mm, wcode,
    vis_m, cape}. `t` is an ISO local-time string ("2026-10-14T06:00"), local to the location.
    """
    days = max(1, min(days, 5))
    notes: List[str] = []
    marine, ulat, ulon, snap_km = await _fetch_marine_cached(lat, lon, days, notes)
    offset = int((marine or {}).get("utc_offset_seconds") or round(lon / 15.0) * 3600)
    weather = await _fetch_weather_cached(lat, lon, days, offset, notes)
    met_used = bool(weather and weather.get("_source") == "met.no")

    base = marine or weather
    if not base or not (base.get("hourly") or {}).get("time"):
        return {"ok": False, "lat": lat, "lon": lon, "used_lat": ulat, "used_lon": ulon, "snap_km": snap_km,
                "utc_offset_s": 0, "hours": [], "notes": notes + ["No forecast data could be retrieved."],
                "has_marine": False, "has_weather": False, "attribution": ATTRIBUTION}

    times = list(base["hourly"]["time"])
    n = len(times)
    mh = (marine or {}).get("hourly") or {}
    wh = (weather or {}).get("hourly") or {}
    w_times = wh.get("time") or []
    w_index = {t: i for i, t in enumerate(w_times)}

    def w(name: str) -> List[Optional[float]]:
        raw = _series(wh, name, len(w_times))
        return [raw[w_index[t]] if t in w_index else None for t in times]

    wind = w("wind_speed_10m")
    gust = w("wind_gusts_10m")
    series = {
        "wave_h": _series(mh, "wave_height", n), "wave_period": _series(mh, "wave_period", n),
        "wave_dir": _series(mh, "wave_direction", n), "wind_wave_h": _series(mh, "wind_wave_height", n),
        "swell_h": _series(mh, "swell_wave_height", n), "swell_period": _series(mh, "swell_wave_period", n),
        "current_kmh": _series(mh, "ocean_current_velocity", n), "current_dir": _series(mh, "ocean_current_direction", n),
        "sst_c": _series(mh, "sea_surface_temperature", n), "sea_level_m": _series(mh, "sea_level_height_msl", n),
        "wind_kmh": wind, "gust_kmh": gust,
        # only for the MET Norway fallback: an explicit ESTIMATE, kept separate so it can never pass as a measurement
        "gust_est_kmh": [round(v * GUST_FACTOR_ESTIMATE, 1) if (met_used and g is None and v is not None) else None
                         for v, g in zip(wind, gust)],
        "precip_mm": w("precipitation"), "wcode": w("weather_code"), "vis_m": w("visibility"), "cape": w("cape"),
    }
    hours = [{"t": times[i], **{k: v[i] for k, v in series.items()}} for i in range(n)]
    attribution = ATTRIBUTION + (" Wind/rain fallback: MET Norway (api.met.no)." if met_used else "")
    return {
        "ok": True, "lat": lat, "lon": lon, "used_lat": ulat, "used_lon": ulon, "snap_km": round(snap_km, 1),
        "utc_offset_s": int(base.get("utc_offset_seconds") or offset), "hours": hours, "notes": notes,
        "has_marine": bool(marine and _has_waves(marine)), "has_weather": bool(weather),
        "gust_estimated": met_used, "attribution": attribution,
    }
