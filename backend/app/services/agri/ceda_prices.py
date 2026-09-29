"""
ceda_prices.py — historical mandi price TREND (average, CAGR) from CEDA's
Agri Market API (Centre for Economic Data & Analysis, Ashoka University),
as a secondary, optional layer alongside mandi.py's live data.gov.in feed.

WHY THIS EXISTS: data.gov.in gives (when it isn't timing out) TODAY's price.
It has no history endpoint, so "is this crop's price trending up or down"
was previously not answerable at all. CEDA maintains a cleaned copy of the
same underlying Agmarknet data back to 2000, explicitly because data.gov.in
is unreliable — but per third-party integrations, CEDA's own data lags
roughly a few months behind live. That's stated honestly to the person, not
hidden: every value this module returns carries the actual as_of date of the
data used, so "average price" and "CAGR" are never presented as more current
than they are.

WIRE FORMAT verified against a real, working third-party client (Dhwanitisshah/
agriopt on GitHub) that documents where CEDA's own published OpenAPI schema is
wrong (do not trust the schema at api.ceda.ashoka.edu.in/documentation/ over
this):
  Base:  https://api.ceda.ashoka.edu.in/v1
  GET  /agmarknet/commodities  -> {"output": {"data": [{"commodity_id", "commodity_name"}, ...]}}
  GET  /agmarknet/geographies  -> {"output": {"data": [{"census_state_id", "census_state_name",
                                    "census_district_id", "census_district_name"}, ...]}}
                                    (one row per DISTRICT, not nested by state -- dedupe for a state lookup)
  POST /agmarknet/prices  body={"commodity_id", "state_id", "from_date", "to_date"}
       -> {"output": {"data": [{"date", "commodity_id", "census_state_id",
                                 "min_price", "max_price", "modal_price"}, ...]}}  -- state-level DAILY records
Auth: `Authorization: Bearer <CEDA_API_KEY>`.
Rate limit: 40 requests / rolling hour (`RateLimit-Policy` response header),
with `Retry-After` (seconds) on 429. Not a requests/sec figure -- this module
leans on its own caching (commodities/geographies cached 30 days, a given
crop+state trend cached 7 days) to stay well under that budget rather than
pacing individual calls, and treats a 429 as "not available right now" rather
than blocking on the wait.
"""

import logging
import statistics
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import httpx

from ...core.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.ceda.ashoka.edu.in/v1"
LOOKUP_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60   # commodities/geographies are static reference lists
TREND_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60     # keeps well under the 40-req/hour budget across repeat views
TREND_YEARS = 5


class CedaRateLimited(Exception):
    def __init__(self, retry_after_seconds: Optional[int]):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"CEDA rate limit hit, retry after {retry_after_seconds}s")


_lookup_cache: Dict[str, Dict[str, Any]] = {}   # "commodities" | "geographies" -> {"data": {...}, "cached_at": t}
_trend_cache: Dict[str, Dict[str, Any]] = {}    # "commodity|state" -> {"data": {...}, "cached_at": t}


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {settings.CEDA_API_KEY}"}


async def _request(client: httpx.AsyncClient, method: str, path: str, **kwargs) -> Any:
    resp = await client.request(method, f"{BASE_URL}{path}", headers=_headers(), timeout=30, **kwargs)
    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        raise CedaRateLimited(int(retry_after) if retry_after and retry_after.isdigit() else None)
    resp.raise_for_status()
    return resp.json()["output"]["data"]


async def _get_commodity_id_map(client: httpx.AsyncClient) -> Dict[str, int]:
    cached = _lookup_cache.get("commodities")
    if cached and (time.time() - cached["cached_at"]) < LOOKUP_CACHE_TTL_SECONDS:
        return cached["data"]
    rows = await _request(client, "GET", "/agmarknet/commodities")
    id_map = {r["commodity_name"].strip().lower(): r["commodity_id"] for r in rows}
    _lookup_cache["commodities"] = {"data": id_map, "cached_at": time.time()}
    return id_map


async def _get_state_id_map(client: httpx.AsyncClient) -> Dict[str, int]:
    cached = _lookup_cache.get("geographies")
    if cached and (time.time() - cached["cached_at"]) < LOOKUP_CACHE_TTL_SECONDS:
        return cached["data"]
    rows = await _request(client, "GET", "/agmarknet/geographies")   # one row per district; dedupe to states
    id_map: Dict[str, int] = {}
    for r in rows:
        id_map[r["census_state_name"].strip().lower()] = r["census_state_id"]
    _lookup_cache["geographies"] = {"data": id_map, "cached_at": time.time()}
    return id_map


def _extract_price_points(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in rows or []:
        d, p = r.get("date"), r.get("modal_price")
        if d and p is not None:
            try:
                out.append({"date": str(d)[:10], "price": float(p)})
            except (TypeError, ValueError):
                continue
    return out


def compute_trend(points: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pure function, independently testable: average price + CAGR from a
    list of {date, price} points, no network/schema dependency."""
    if not points:
        return None
    points = sorted(points, key=lambda p: p["date"])
    prices = [p["price"] for p in points if p["price"] and p["price"] > 0]
    if not prices:
        return None
    avg = statistics.mean(prices)
    as_of = points[-1]["date"]

    # CAGR from first vs last available YEAR's average (smooths out single-day/
    # single-month noise on both ends), not first-point-vs-last-point.
    by_year: Dict[str, List[float]] = {}
    for p in points:
        if p["price"] and p["price"] > 0:
            by_year.setdefault(p["date"][:4], []).append(p["price"])
    years = sorted(by_year)
    cagr = None
    if len(years) >= 2:
        first_year, last_year = years[0], years[-1]
        n = int(last_year) - int(first_year)
        if n > 0:
            first_avg = statistics.mean(by_year[first_year])
            last_avg = statistics.mean(by_year[last_year])
            if first_avg > 0:
                cagr = ((last_avg / first_avg) ** (1 / n) - 1) * 100

    return {
        "avg_price_modal": round(avg, 2),
        "cagr_pct": round(cagr, 1) if cagr is not None else None,
        "as_of_date": as_of,
        "period": f"{years[0]}–{years[-1]}" if years else None,
        "n_points": len(prices),
    }


# /agmarknet/prices requires a state_id -- there's no national/all-India option in
# the verified schema. Rajasthan is Vayu's primary use area (Bharatpur etc.), so
# that's the sensible default when the caller doesn't have a specific state in
# hand; a caller that does pass one always wins.
DEFAULT_STATE = "Rajasthan"


async def get_price_trend(commodity: str, state: Optional[str] = None) -> Dict[str, Any]:
    """Historical average price and CAGR for a commodity, from CEDA. Never
    raises — an API failure returns {"available": False, "error": ...} so a
    broken secondary source never blocks the primary (live, data.gov.in)
    price the revenue estimate actually depends on."""
    if not settings.CEDA_API_KEY:
        return {"available": False, "error": "CEDA_API_KEY not configured"}

    state = state or DEFAULT_STATE
    cache_key = f"{commodity.strip().lower()}|{state.strip().lower()}"
    cached = _trend_cache.get(cache_key)
    if cached and (time.time() - cached["cached_at"]) < TREND_CACHE_TTL_SECONDS:
        return cached["data"]

    try:
        async with httpx.AsyncClient() as client:
            commodities = await _get_commodity_id_map(client)
            commodity_id = commodities.get(commodity.strip().lower())
            if commodity_id is None:
                result = {"available": False, "error": f"commodity '{commodity}' not found in CEDA lookup"}
                _trend_cache[cache_key] = {"data": result, "cached_at": time.time()}
                return result

            states = await _get_state_id_map(client)
            state_id = states.get(state.strip().lower())
            if state_id is None:
                result = {"available": False, "error": f"state '{state}' not found in CEDA lookup"}
                _trend_cache[cache_key] = {"data": result, "cached_at": time.time()}
                return result

            body = {
                "commodity_id": commodity_id, "state_id": state_id,
                "from_date": (date.today() - timedelta(days=365 * TREND_YEARS)).isoformat(),
                "to_date": date.today().isoformat(),
            }
            rows = await _request(client, "POST", "/agmarknet/prices", json=body)
            points = _extract_price_points(rows)
            trend = compute_trend(points)
            if trend is None:
                result = {"available": False, "error": "no usable price points in CEDA response"}
            else:
                result = {"available": True, "source": "CEDA Agri Market Data (Ashoka University)", **trend}
            _trend_cache[cache_key] = {"data": result, "cached_at": time.time()}
            return result
    except CedaRateLimited as e:
        logger.warning(f"CEDA rate-limited for '{commodity}'/{state}, retry_after={e.retry_after_seconds}s")
        result = {"available": False, "error": f"CEDA rate limit hit (retry after {e.retry_after_seconds}s)"}
        # Cache the rate-limit result too (short-lived) -- otherwise every crop in the
        # same suitability call re-hits the same 429 instead of backing off together.
        _trend_cache[cache_key] = {"data": result, "cached_at": time.time() - TREND_CACHE_TTL_SECONDS + 300}
        return result
    except Exception as e:
        body_snippet = ""
        resp_obj = getattr(e, "response", None)
        if resp_obj is not None:
            try:
                body_snippet = f" — body: {resp_obj.text[:300]}"
            except Exception:
                pass
        logger.warning(f"CEDA price trend fetch failed for '{commodity}'/{state}: {type(e).__name__}: {e}{body_snippet}")
        return {"available": False, "error": f"{type(e).__name__}: {e}{body_snippet}"}
