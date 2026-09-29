"""
ceda_prices.py — historical mandi price TREND (average, CAGR) from CEDA's
Agri Market API (Centre for Economic Data & Analysis, Ashoka University),
as a secondary, optional layer alongside mandi.py's live data.gov.in feed.

WHY THIS EXISTS: data.gov.in gives (when it isn't timing out) TODAY's price.
It has no history endpoint, so "is this crop's price trending up or down"
was previously not answerable at all. CEDA maintains a cleaned copy of the
same underlying Agmarknet data back to 2000, explicitly because data.gov.in
is unreliable (documented by other projects integrating both: "data.gov.in
is frequently down / WAF-blocked" vs "CEDA: stable") — but per those same
sources, CEDA's own data lags roughly a few months behind live. That's
stated honestly to the person, not hidden: every value this module returns
carries the actual as_of date of the data used, so "average price" and
"CAGR" are never presented as more current than they are.

⚠️ UNVERIFIED WIRE FORMAT: CEDA's endpoints, auth and response shape below
are reconstructed from third-party integration docs and an academic paper's
data appendix, NOT from a live test against api.ceda.ashoka.edu.in — that
host isn't reachable from this build environment, and using the API requires
a CEDA_API_KEY obtained via CEDA's own email signup (api.ceda.ashoka.edu.in),
which nobody but the account holder can do. First live run will likely need
one or more of: the exact base path, the exact id-lookup field names, or the
exact /prices response envelope adjusted to match what CEDA actually returns
— check the logged raw response on the first failure and fix the three
_extract_* / _paths helpers below accordingly; the trend math (avg, CAGR)
past that point doesn't depend on any of those specifics and is independently
unit-tested.

Endpoints per CEDA's own paper-cited API appendix (agmarknet.ceda.ashoka.edu.in/api/)
and its documented Swagger grouping (api.ceda.ashoka.edu.in — "/agmarknet/prices,
obtain prices for a commodity at the national/state/district/market level"):
  GET /agmarknet/states                          -> [{id, name}, ...]
  GET /agmarknet/commodities                      -> [{id, name}, ...]
  GET /agmarknet/districts?state_id=N             -> [{id, name}, ...]
  GET /agmarknet/prices?commodity_id=..&state_id=..&from=YYYY-MM-DD&to=YYYY-MM-DD
                                                    -> [{date, modal_price, ...}, ...]
Auth: `Authorization: Bearer <CEDA_API_KEY>` (per third-party integration notes).
"""

import logging
import statistics
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import httpx

from ...core.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.ceda.ashoka.edu.in/agmarknet"
LOOKUP_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60   # states/commodities/districts are static reference lists
TREND_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60     # monthly-granularity data; no need to refetch often
TREND_YEARS = 5

_lookup_cache: Dict[str, Dict[str, Any]] = {}   # "states" | "commodities" | "districts:<state_id>" -> {"data": {name_lower: id}, "cached_at": t}
_trend_cache: Dict[str, Dict[str, Any]] = {}    # "commodity|state" -> {"data": {...}, "cached_at": t}


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {settings.CEDA_API_KEY}"}


async def _get(client: httpx.AsyncClient, path: str, params: Dict[str, Any]) -> Any:
    resp = await client.get(f"{BASE_URL}{path}", params=params, headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def _extract_id_map(raw: Any) -> Dict[str, int]:
    """Best-effort normalizer: CEDA's exact envelope (bare list vs {"data": [...]}
    vs {"results": [...]}) isn't confirmed, so this tries the shapes seen across
    similar data.gov.in-adjacent APIs rather than assuming one."""
    items = raw
    if isinstance(raw, dict):
        items = raw.get("data") or raw.get("results") or raw.get("records") or []
    out: Dict[str, int] = {}
    for item in items or []:
        name = item.get("name") or item.get("state_name") or item.get("district_name") or item.get("commodity_name")
        id_ = item.get("id") or item.get("state_id") or item.get("district_id") or item.get("commodity_id")
        if name is not None and id_ is not None:
            out[str(name).strip().lower()] = id_
    return out


async def _lookup(client: httpx.AsyncClient, kind: str, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    cache_key = kind if not params else f"{kind}:{params}"
    cached = _lookup_cache.get(cache_key)
    if cached and (time.time() - cached["cached_at"]) < LOOKUP_CACHE_TTL_SECONDS:
        return cached["data"]
    raw = await _get(client, path, params or {})
    id_map = _extract_id_map(raw)
    _lookup_cache[cache_key] = {"data": id_map, "cached_at": time.time()}
    return id_map


def _extract_price_points(raw: Any) -> List[Dict[str, Any]]:
    items = raw
    if isinstance(raw, dict):
        items = raw.get("data") or raw.get("results") or raw.get("records") or []
    out = []
    for r in items or []:
        d = r.get("date") or r.get("arrival_date") or r.get("price_date")
        p = r.get("modal_price") or r.get("model_price") or r.get("price")
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


async def get_price_trend(commodity: str, state: Optional[str] = None) -> Dict[str, Any]:
    """Historical average price and CAGR for a commodity, from CEDA. Never
    raises — a schema mismatch or network failure returns {"available": False,
    "error": ...} so a broken secondary source never blocks the primary
    (live, data.gov.in) price the revenue estimate actually depends on."""
    if not settings.CEDA_API_KEY:
        return {"available": False, "error": "CEDA_API_KEY not configured"}

    cache_key = f"{commodity.strip().lower()}|{(state or '').strip().lower()}"
    cached = _trend_cache.get(cache_key)
    if cached and (time.time() - cached["cached_at"]) < TREND_CACHE_TTL_SECONDS:
        return cached["data"]

    try:
        async with httpx.AsyncClient() as client:
            commodities = await _lookup(client, "commodities", "/commodities")
            commodity_id = commodities.get(commodity.strip().lower())
            if commodity_id is None:
                result = {"available": False, "error": f"commodity '{commodity}' not found in CEDA lookup"}
                _trend_cache[cache_key] = {"data": result, "cached_at": time.time()}
                return result

            params = {"commodity_id": commodity_id,
                       "from": (date.today() - timedelta(days=365 * TREND_YEARS)).isoformat(),
                       "to": date.today().isoformat()}
            if state:
                states = await _lookup(client, "states", "/states")
                state_id = states.get(state.strip().lower())
                if state_id is not None:
                    params["state_id"] = state_id

            raw = await _get(client, "/prices", params)
            points = _extract_price_points(raw)
            trend = compute_trend(points)
            if trend is None:
                result = {"available": False, "error": "no usable price points in CEDA response"}
            else:
                result = {"available": True, "source": "CEDA Agri Market Data (Ashoka University)", **trend}
            _trend_cache[cache_key] = {"data": result, "cached_at": time.time()}
            return result
    except Exception as e:
        body_snippet = ""
        resp_obj = getattr(e, "response", None)
        if resp_obj is not None:
            try:
                body_snippet = f" — body: {resp_obj.text[:300]}"
            except Exception:
                pass
        logger.warning(f"CEDA price trend fetch failed for '{commodity}': {type(e).__name__}: {e}{body_snippet}")
        return {"available": False, "error": f"{type(e).__name__}: {e}{body_snippet}"}
