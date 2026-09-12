"""
macro.py — free macro-economic context so a commodity price move
doesn't sit in isolation ("oil is up 4%" means very different things
depending on what rates/inflation are doing).

Two free sources, no cost either way:
  - FRED (St. Louis Fed) — https://fred.stlouisfed.org/docs/api/fred/
    Free, but DOES require a free API key (FRED_API_KEY env var,
    registered at https://fredaccount.stlouisfed.org/apikeys). Without
    a key set, this degrades to World Bank data only rather than
    erroring — see get_macro_snapshot().
  - World Bank — https://api.worldbank.org/v2/ — free, genuinely no
    key required at all.

Series picked are the ones a commodity/macro trader actually glances
at, not an exhaustive econ dashboard: US Fed funds rate, CPI YoY, and
10-year Treasury yield from FRED; global GDP growth and inflation from
World Bank as the slower-moving backdrop.
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
    "global_gdp_growth": "NY.GDP.MKTP.KD.ZG",
    "global_inflation": "FP.CPI.TOTL.ZG",
}

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


async def _world_bank_latest(indicator: str) -> Optional[dict]:
    url = f"https://api.worldbank.org/v2/country/WLD/indicator/{indicator}"
    params = {"format": "json", "per_page": 10, "mrnev": 1}  # most recent non-empty value
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as e:
        logger.warning(f"World Bank fetch failed for {indicator}: {type(e).__name__}: {e}")
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


async def _world_bank_history(indicator: str, years: int = 15) -> list[dict]:
    url = f"https://api.worldbank.org/v2/country/WLD/indicator/{indicator}"
    from datetime import datetime
    this_year = datetime.utcnow().year
    params = {"format": "json", "per_page": years + 2, "date": f"{this_year - years}:{this_year}"}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as e:
        logger.warning(f"World Bank history fetch failed for {indicator}: {type(e).__name__}: {e}")
        return []
    if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
        return []
    rows = [r for r in payload[1] if r.get("value") is not None]
    rows.sort(key=lambda r: r.get("date", ""))
    return [{"date": r["date"], "value": round(r["value"], 2)} for r in rows]


async def get_world_bank_history(label: str, years: int = 15) -> dict:
    """label: one of WORLD_BANK_INDICATORS's keys (global_gdp_growth/global_inflation)."""
    indicator = WORLD_BANK_INDICATORS.get(label)
    if not indicator:
        return {"label": label, "points": [], "error": f"Unknown indicator. Valid: {list(WORLD_BANK_INDICATORS)}"}
    points = await _world_bank_history(indicator, years)
    return {"label": label, "indicator": indicator, "points": points}


async def get_macro_snapshot() -> dict[str, Any]:
    cache_key = "macro_snapshot"
    now = time.time()
    if cache_key in _cache and now - _cache[cache_key][0] < CACHE_TTL_SECONDS:
        return _cache[cache_key][1]

    from ...core.config import settings
    fred_key = getattr(settings, "FRED_API_KEY", "") or ""

    result: dict[str, Any] = {"fred_configured": bool(fred_key), "us": {}, "global": {}}

    if fred_key:
        for label, series_id in FRED_SERIES.items():
            val = await _fred_latest(series_id, fred_key)
            if val:
                result["us"][label] = val
    else:
        logger.info("macro: FRED_API_KEY not set — skipping US series, World Bank data only")

    for label, indicator in WORLD_BANK_INDICATORS.items():
        val = await _world_bank_latest(indicator)
        if val:
            result["global"][label] = val

    _cache[cache_key] = (now, result)
    return result
