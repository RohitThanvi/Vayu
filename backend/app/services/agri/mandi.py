"""
mandi.py — market (mandi) price overlay.

Ties a risk/condition read to "what's this crop worth right now" — the
harvest-timing decision no pure remote-sensing competitor surfaces well.

Uses data.gov.in's public Agmarknet daily mandi price API (India), the
same data.gov.in account/key as the CPCB air-quality feed (see
services/intel/air_quality.py) — one key covers both.

This resource is one of the flakier ones on data.gov.in in practice — even
small, filtered requests routinely take 30-60s+ or time out outright, worse
than the much larger CPCB AQI pull in air_quality.py. Two things fix the
"read timeout" this caused: (1) an in-memory TTL cache, since mandi prices
only change daily anyway, so most requests should never touch the network;
(2) the same tiered-timeout retry as air_quality.py for the (now rarer)
cache-miss request. A stale cache entry is served on total failure — matches
the "stale beats empty" pattern used elsewhere in this project.
"""

import logging
import time
from typing import Any, Dict, Optional

import httpx

from ...core.config import settings

logger = logging.getLogger(__name__)

RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"  # "Current Daily Price of Various Commodities from Various Markets (Mandi)"
BASE_URL = f"https://api.data.gov.in/resource/{RESOURCE_ID}"

CACHE_TTL_SECONDS = 60 * 60 * 3  # mandi prices update once/day; 3h keeps the UI fresh-feeling without hammering a flaky endpoint
_cache: Dict[str, Dict[str, Any]] = {}  # key -> {"data": ..., "cached_at": float}


def _cache_key(commodity, state, district, limit) -> str:
    return f"{(commodity or '').strip().lower()}|{(state or '').strip().lower()}|{(district or '').strip().lower()}|{limit}"


async def get_mandi_prices(commodity: Optional[str] = None, state: Optional[str] = None,
                            district: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
    api_key = settings.AQI_API_KEY
    if not api_key:
        logger.warning("mandi price fetch skipped — AQI_API_KEY not configured")
        return {"records": [], "error": "AQI_API_KEY not configured", "source": "data.gov.in Agmarknet"}

    key = _cache_key(commodity, state, district, limit)
    cached = _cache.get(key)
    if cached and (time.time() - cached["cached_at"]) < CACHE_TTL_SECONDS:
        return {**cached["data"], "cache": "hit"}

    params = {"api-key": api_key, "format": "json", "limit": str(limit)}
    if commodity:
        params["filters[commodity]"] = commodity
    if state:
        params["filters[state]"] = state
    if district:
        params["filters[district]"] = district

    # Tiered timeout, same pattern as air_quality.py's CPCB fetch: try the real
    # request with a generous timeout first, then retry once with a smaller
    # limit/timeout if that still doesn't come back in time. This resource is
    # small (limit<=20 typically) so the retry is about outrunning data.gov.in's
    # own latency, not payload size the way it is for the AQI pull.
    # Observed in production: even the (limit,60)/(10,30) tiers below can both
    # miss when data.gov.in is having a slow patch (logged ReadTimeouts at
    # 30-32s against a 30s timeout). Widened both tiers; still two attempts,
    # not more -- a third tier would push the worst case for one crop's
    # lookup well past what's reasonable inside one page load, especially
    # with add_profit_estimates() calling this for several crops.
    attempts = [(limit, 75), (min(limit, 10), 45)]
    last_exc = None
    last_detail = None
    for attempt_limit, attempt_timeout in attempts:
        params["limit"] = str(attempt_limit)
        t0 = time.time()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(BASE_URL, params=params, timeout=attempt_timeout)
                resp.raise_for_status()
                data = resp.json()
                if "records" not in data:
                    # Same data.gov.in quirk as air_quality.py: an invalid/
                    # not-yet-activated key or wrong resource id often comes
                    # back as HTTP 200 with a {"message": "..."} body and no
                    # "records" key, which raise_for_status() can't catch since
                    # it only looks at the status code.
                    detail = data.get("message") or data.get("error") or str(data)[:300]
                    raise ValueError(f"data.gov.in returned no 'records' key — {detail}")
            logger.info(f"mandi: fetched limit={attempt_limit} in {time.time()-t0:.1f}s")
            break
        except Exception as e:
            last_exc = e
            body_snippet = ""
            resp_obj = getattr(e, "response", None)
            if resp_obj is not None:
                try:
                    body_snippet = f" — body: {resp_obj.text[:300]}"
                except Exception:
                    pass
            logger.warning(f"mandi fetch attempt (limit={attempt_limit}, timeout={attempt_timeout}s) failed after {time.time()-t0:.1f}s: {type(e).__name__}: {e}{body_snippet}")
            last_detail = f"{type(e).__name__}: {e}{body_snippet}" if str(e) else f"{type(e).__name__}{body_snippet}"
    else:
        if cached:
            logger.warning(f"mandi: all attempts failed, serving stale cache from {time.time()-cached['cached_at']:.0f}s ago")
            return {**cached["data"], "cache": "stale", "error": last_detail}
        return {"records": [], "error": last_detail or "unknown error", "source": "data.gov.in Agmarknet"}

    records = data.get("records", [])
    parsed = [
        {
            "commodity": r.get("commodity"), "variety": r.get("variety"), "market": r.get("market"),
            "district": r.get("district"), "state": r.get("state"),
            "min_price": r.get("min_price"), "max_price": r.get("max_price"), "modal_price": r.get("modal_price"),
            "arrival_date": r.get("arrival_date"),
        }
        for r in records
    ]
    result = {"records": parsed, "count": len(parsed), "source": "data.gov.in Agmarknet"}
    _cache[key] = {"data": result, "cached_at": time.time()}
    return {**result, "cache": "miss"}
