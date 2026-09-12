"""
macro.py — free macro-economic context so a commodity price move
doesn't sit in isolation ("oil is up 4%" means very different things
depending on what rates/inflation are doing).

Two free sources, no cost either way:
  - FRED (St. Louis Fed) — https://fred.stlouisfed.org/docs/api/fred/
    Free, but DOES require a free API key (FRED_API_KEY env var,
    registered at https://fredaccount.stlouisfed.org/apikeys). Without
    a key set, this degrades to World Bank data only rather than
    erroring — see get_macro_snapshot(). US-only (FRED is the Federal
    Reserve's own series catalog).
  - World Bank — https://api.worldbank.org/v2/ — free, genuinely no
    key required at all, and per-country: the SAME indicator query
    that gives the global aggregate (country=WLD) works identically
    for any country code, so India (country=IND) is not a new
    integration — see WORLD_BANK_COUNTRIES / india below.

Series picked are the ones a commodity/macro trader actually glances
at, not an exhaustive econ dashboard: US Fed funds rate, CPI YoY, and
10-year Treasury yield from FRED; GDP growth and inflation from World
Bank for both the global aggregate and India specifically.

Note on India rates specifically: the RBI (India's central bank) has
NO official free public data API — every "RBI repo rate API" that
turns up is an unofficial third-party wrapper that scrapes rbi.org.in
and charges per call. Rather than build on something that isn't
actually free/official and can break the moment RBI's site layout
changes (see mandi.py/air_quality.py's data.gov.in troubles for why
that's worth avoiding), India's repo rate is left out until/unless a
real official API shows up. GDP growth and inflation via World Bank
are the reliable, genuinely-free India data available right now.
"""

import logging
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

FRED_SERIES = {
    "fed_funds_rate": "FEDFUNDS",
    "cpi_yoy": "CPIAUCSL",       # raw index; caller/consumer computes YoY from last 13 points
    "treasury_10y": "DGS10",
}

WORLD_BANK_INDICATORS = {
    "gdp_growth": "NY.GDP.MKTP.KD.ZG",
    "inflation": "FP.CPI.TOTL.ZG",
}
# World Bank's own aggregate code for "the whole world" is WLD (not a
# real ISO country code) — everything else is the actual ISO-3 code.
WORLD_BANK_COUNTRIES = {"global": "WLD", "india": "IND"}

_cache: dict[str, tuple[float, Any]] = {}
CACHE_TTL_SECONDS = 6 * 3600  # macro series update daily/monthly at most; no need to poll often


async def _fred_latest(series_id: str, api_key: str) -> Optional[dict]:
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id, "api_key": api_key, "file_type": "json",
        "sort_order": "desc", "limit": 13,  # 13 for CPI so a YoY delta can be computed (12 months back)
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=15)
            resp.raise_for_status()
            obs = resp.json().get("observations", [])
    except Exception as e:
        logger.warning(f"FRED fetch failed for {series_id}: {type(e).__name__}: {e}")
        return None
    valid = [o for o in obs if o.get("value") not in (".", None)]
    if not valid:
        return None
    latest = valid[0]
    result = {"value": float(latest["value"]), "date": latest["date"]}
    if series_id == "CPIAUCSL" and len(valid) >= 13:
        year_ago = float(valid[12]["value"])
        result["yoy_pct"] = round((result["value"] - year_ago) / year_ago * 100, 2)
    return result


async def _world_bank_latest(indicator: str, country: str = "WLD") -> Optional[dict]:
    url = f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator}"
    params = {"format": "json", "per_page": 10, "mrnev": 1}  # most recent non-empty value
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as e:
        logger.warning(f"World Bank fetch failed for {indicator} ({country}): {type(e).__name__}: {e}")
        return None
    if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
        return None
    for row in payload[1]:
        if row.get("value") is not None:
            return {"value": round(row["value"], 2), "date": row.get("date")}
    return None


async def _fred_history(series_id: str, api_key: str, months: int = 24) -> list[dict]:
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id, "api_key": api_key, "file_type": "json",
        "sort_order": "asc", "limit": months + 1,
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=15)
            resp.raise_for_status()
            obs = resp.json().get("observations", [])
    except Exception as e:
        logger.warning(f"FRED history fetch failed for {series_id}: {type(e).__name__}: {e}")
        return []
    return [
        {"date": o["date"], "value": float(o["value"])}
        for o in obs[-months:] if o.get("value") not in (".", None)
    ]


async def get_fred_history(label: str, months: int = 24) -> dict:
    """label: one of FRED_SERIES's keys (fed_funds_rate/cpi_yoy/treasury_10y)."""
    from ...core.config import settings
    series_id = FRED_SERIES.get(label)
    if not series_id:
        return {"label": label, "points": [], "error": f"Unknown series. Valid: {list(FRED_SERIES)}"}
    api_key = getattr(settings, "FRED_API_KEY", "") or ""
    if not api_key:
        return {"label": label, "points": [], "error": "FRED_API_KEY not configured"}
    points = await _fred_history(series_id, api_key, months)
    return {"label": label, "series_id": series_id, "points": points}


async def _world_bank_history(indicator: str, country: str = "WLD", years: int = 15) -> list[dict]:
    url = f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator}"
    from datetime import datetime
    this_year = datetime.utcnow().year
    params = {"format": "json", "per_page": years + 2, "date": f"{this_year - years}:{this_year}"}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as e:
        logger.warning(f"World Bank history fetch failed for {indicator} ({country}): {type(e).__name__}: {e}")
        return []
    if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
        return []
    rows = [r for r in payload[1] if r.get("value") is not None]
    rows.sort(key=lambda r: r.get("date", ""))
    return [{"date": r["date"], "value": round(r["value"], 2)} for r in rows]


async def get_world_bank_history(label: str, region: str = "global", years: int = 15) -> dict:
    """label: one of WORLD_BANK_INDICATORS's keys (gdp_growth/inflation).
    region: one of WORLD_BANK_COUNTRIES's keys (global/india)."""
    indicator = WORLD_BANK_INDICATORS.get(label)
    country = WORLD_BANK_COUNTRIES.get(region)
    if not indicator:
        return {"label": label, "points": [], "error": f"Unknown indicator. Valid: {list(WORLD_BANK_INDICATORS)}"}
    if not country:
        return {"label": label, "points": [], "error": f"Unknown region. Valid: {list(WORLD_BANK_COUNTRIES)}"}
    points = await _world_bank_history(indicator, country, years)
    return {"label": label, "region": region, "indicator": indicator, "points": points}


async def get_macro_snapshot() -> dict[str, Any]:
    cache_key = "macro_snapshot"
    now = time.time()
    if cache_key in _cache and now - _cache[cache_key][0] < CACHE_TTL_SECONDS:
        return _cache[cache_key][1]

    from ...core.config import settings
    fred_key = getattr(settings, "FRED_API_KEY", "") or ""

    result: dict[str, Any] = {"fred_configured": bool(fred_key), "us": {}, "global": {}, "india": {}}

    if fred_key:
        for label, series_id in FRED_SERIES.items():
            val = await _fred_latest(series_id, fred_key)
            if val:
                result["us"][label] = val
    else:
        logger.info("macro: FRED_API_KEY not set — skipping US series, World Bank data only")

    for region_key, country_code in WORLD_BANK_COUNTRIES.items():
        for label, indicator in WORLD_BANK_INDICATORS.items():
            val = await _world_bank_latest(indicator, country_code)
            if val:
                result[region_key][label] = val

    _cache[cache_key] = (now, result)
    return result
