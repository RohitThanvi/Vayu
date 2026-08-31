"""
commodity_prices.py — a "market intelligence" layer for the ticker at the
bottom of the screen: global commodity futures prices (crude oil, natural
gas, metals, agri commodities).

History: originally used Alpha Vantage's free Commodities API, but its
25-requests/day cap turned out unworkable in production -- Render's free
tier cold-starts the process repeatedly (each restart reset the in-process
refresh timer), and even with request spacing and a same-day cache guard,
the daily budget was too thin to be reliable. Switched to Yahoo Finance's
unofficial chart API instead (query1.finance.yahoo.com/v8/finance/chart) --
completely keyless, no formal daily cap, and gives real (if ~15-20min
delayed) futures prices instead of Alpha Vantage's monthly-resolution data.

Honest tradeoff, stated plainly: this is an unofficial, reverse-engineered
endpoint, not a documented/contracted API -- it's extremely widely used
(countless tools and libraries rely on exactly this endpoint) and has been
stable for years, but Yahoo could change its shape or soft-block abusive
traffic without notice. That's a real but generally low production risk,
different in kind from Alpha Vantage's hard 25/day wall -- there's no
formal SLA either way, but nothing here depends on one.

Still NOT MCX (India's commodity exchange) real-time data -- MCX's actual
live feed is a paid exchange subscription with no free/legal alternative;
this shows global futures prices instead.
"""

import asyncio
import logging
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 3 * 60 * 60   # refresh every few hours — no hard daily cap here, but still polite/bounded

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# (Yahoo futures symbol, display label, unit) — real CME/ICE front-month
# contract tickers, not a proprietary commodity code the way Alpha
# Vantage used.
COMMODITIES = [
    ("CL=F", "Crude Oil (WTI)", "USD/barrel"),
    ("BZ=F", "Crude Oil (Brent)", "USD/barrel"),
    ("NG=F", "Natural Gas", "USD/MMBtu"),
    ("HG=F", "Copper", "USD/lb"),
    ("GC=F", "Gold", "USD/oz"),
    ("ZW=F", "Wheat", "USD/bushel"),
    ("ZC=F", "Corn", "USD/bushel"),
    ("CT=F", "Cotton", "USD/lb"),
    ("SB=F", "Sugar", "USD/lb"),
    ("KC=F", "Coffee", "USD/lb"),
]

_cache: Dict[str, Any] = {"commodities": [], "cached_at": 0.0, "last_error": None}
_lock = threading.Lock()

_HEADERS = {
    # Yahoo's endpoint is known to reject requests with no browser-like
    # User-Agent (community-documented behavior for this specific
    # unofficial endpoint) — this isn't spoofing a browser session,
    # just avoiding the default python-httpx UA that gets a blanket reject.
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


async def _fetch_one(client: httpx.AsyncClient, symbol: str, label: str, unit: str) -> Optional[Dict[str, Any]]:
    url = YAHOO_CHART_URL.format(symbol=symbol)
    try:
        resp = await client.get(url, params={"interval": "1d", "range": "5d"}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"Commodity fetch failed for {symbol}: {type(e).__name__}: {e}")
        return None

    try:
        result = data["chart"]["result"][0]
        meta = result.get("meta", {})
        price = meta.get("regularMarketPrice")
        prev_close = meta.get("previousClose") or meta.get("chartPreviousClose")
    except (KeyError, IndexError, TypeError):
        logger.warning(f"Commodity fetch for {symbol} returned an unexpected response shape")
        return None

    if price is None:
        # Fall back to the last non-null close in the daily series if the
        # meta block didn't have a live quote for some reason (e.g. market
        # closed and Yahoo hasn't populated regularMarketPrice for this cycle).
        try:
            closes = result["indicators"]["quote"][0]["close"]
            for c in reversed(closes):
                if c is not None:
                    price = c
                    break
        except (KeyError, IndexError, TypeError):
            pass

    if price is None:
        return None

    change_pct = None
    if prev_close:
        try:
            change_pct = (float(price) - float(prev_close)) / float(prev_close) * 100
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    return {
        "symbol": symbol,
        "name": label,
        "unit": unit,
        "value": round(float(price), 4),
        "change_pct": round(change_pct, 2) if change_pct is not None else None,
    }


async def refresh(force: bool = False) -> int:
    """Fetch all configured commodities and refresh the cache. Returns the
    count that succeeded (a partial failure still caches whatever
    succeeded rather than discarding it for an all-or-nothing refresh)."""
    if not force:
        with _lock:
            cached_at = _cache["cached_at"]
        if cached_at and (time.time() - cached_at) < CACHE_TTL_SECONDS:
            logger.debug("Commodity refresh skipped — cache is still fresh")
            return len(_cache["commodities"])

    results = []
    async with httpx.AsyncClient(headers=_HEADERS) as client:
        for i, (symbol, label, unit) in enumerate(COMMODITIES):
            if i > 0:
                await asyncio.sleep(0.5)   # light spacing — polite, not because of a known hard limit here
            item = await _fetch_one(client, symbol, label, unit)
            if item:
                results.append(item)

    with _lock:
        if results:
            _cache["commodities"] = results
            _cache["cached_at"] = time.time()
            _cache["last_error"] = None if len(results) == len(COMMODITIES) else f"only {len(results)}/{len(COMMODITIES)} commodities refreshed this cycle"
        else:
            _cache["last_error"] = "all commodity fetches failed this cycle"

    logger.info(f"Commodity prices: {len(results)}/{len(COMMODITIES)} refreshed")
    return len(results)


def get_commodities() -> Dict[str, Any]:
    with _lock:
        return {
            "commodities": list(_cache["commodities"]),
            "cached_at": _cache["cached_at"],
            "last_error": _cache["last_error"],
        }
