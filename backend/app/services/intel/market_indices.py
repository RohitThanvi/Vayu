"""
market_indices.py — global stock market indices, plus a curated set of
"bellwether" equities whose price moves plausibly correlate with the
same chokepoint/supply-chain/sanctions signals Vayu already tracks
(business_risk.py, dark_vessels.py, the strategic-sites layer) — not a
generic finance-app ticker list. Energy majors near chokepoint-adjacent
oil flows, a container shipper, a global logistics bellwether, and two
defense primes (conflict/sanctions-adjacent) sit alongside the broad
indices for exactly that reason.

Reuses commodity_prices.py's _fetch_one() as-is — it's already fully
generic (symbol/label/unit/category in, quote dict out; nothing in its
logic is commodity-specific) — rather than duplicating the same ~40
lines of Yahoo Finance response parsing a second time. Same unofficial
query1.finance.yahoo.com/v8/finance/chart endpoint, same honest
tradeoffs (keyless, no formal SLA, ~15-20min delayed, widely-used and
stable but not a contracted API) — see that file's module docstring
for the fuller reasoning; not repeated here.
"""

import asyncio
import logging
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

from .commodity_prices import _fetch_one, YAHOO_CHART_URL, _HEADERS

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 3 * 60 * 60   # matches commodity_prices — same reasoning, no need for faster than this

# (Yahoo symbol, display label, unit, category)
INDICES = [
    ("^GSPC", "S&P 500", "index", "us"),
    ("^DJI", "Dow Jones Industrial Average", "index", "us"),
    ("^IXIC", "Nasdaq Composite", "index", "us"),
    ("^NSEI", "Nifty 50", "index", "india"),
    ("^BSESN", "BSE Sensex", "index", "india"),
    ("^FTSE", "FTSE 100", "index", "europe"),
    ("^N225", "Nikkei 225", "index", "asia"),
    ("000001.SS", "Shanghai Composite", "index", "asia"),
]

# Deliberately curated, not a generic watchlist — each pick ties back to
# a signal Vayu already tracks elsewhere:
#   - RELIANCE.NS / XOM / CVX: energy majors, relevant to the same oil-
#     chokepoint (Hormuz/Suez) traffic business_risk.py already scores
#   - ZIM / FDX: a container shipper and a global logistics bellwether —
#     equity moves here are one more (imperfect, market-priced) signal
#     alongside the vessel-traffic-based chokepoint score, not a
#     replacement for it
#   - LMT / RTX: defense primes, whose order books/share moves often
#     react to the same conflict/sanctions events geo_tone.py's GDELT
#     tone tracking picks up
BELLWETHER_STOCKS = [
    ("RELIANCE.NS", "Reliance Industries", "equity", "energy"),
    ("XOM", "ExxonMobil", "equity", "energy"),
    ("CVX", "Chevron", "equity", "energy"),
    ("ZIM", "ZIM Integrated Shipping", "equity", "shipping"),
    ("FDX", "FedEx", "equity", "logistics"),
    ("LMT", "Lockheed Martin", "equity", "defense"),
    ("RTX", "RTX Corp (Raytheon)", "equity", "defense"),
]

ALL_SYMBOLS = INDICES + BELLWETHER_STOCKS

_cache: Dict[str, Any] = {"indices": [], "stocks": [], "cached_at": 0.0, "last_error": None}
_lock = threading.Lock()


async def get_history(symbol: str, range_: str = "3mo") -> Dict[str, Any]:
    """Historical daily closes for an index/stock symbol, for charting —
    same shape and same Yahoo endpoint as commodity_prices.get_history,
    just against this module's own symbol list."""
    valid_ranges = {"1mo", "3mo", "6mo", "1y", "2y", "5y"}
    if range_ not in valid_ranges:
        range_ = "3mo"
    label_lookup = {sym: (label, unit) for sym, label, unit, _cat in ALL_SYMBOLS}
    if symbol not in label_lookup:
        return {"symbol": symbol, "points": [], "error": "Unknown symbol"}

    url = YAHOO_CHART_URL.format(symbol=symbol)
    try:
        async with httpx.AsyncClient(headers=_HEADERS) as client:
            resp = await client.get(url, params={"interval": "1d", "range": range_}, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        result = data["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        closes = result["indicators"]["quote"][0]["close"]
        points = [
            {"date": ts, "value": round(float(c), 4)}
            for ts, c in zip(timestamps, closes) if c is not None
        ]
    except Exception as e:
        logger.warning(f"Market history fetch failed for {symbol}: {type(e).__name__}: {e}")
        return {"symbol": symbol, "points": [], "error": f"{type(e).__name__}: {e}"}

    label, unit = label_lookup[symbol]
    return {"symbol": symbol, "name": label, "unit": unit, "range": range_, "points": points}


async def refresh(force: bool = False) -> int:
    """Fetch all configured indices + bellwether stocks and refresh the
    cache. A partial failure still caches whatever succeeded, same
    all-or-nothing-avoidance reasoning as commodity_prices.refresh."""
    if not force:
        with _lock:
            cached_at = _cache["cached_at"]
        if cached_at and (time.time() - cached_at) < CACHE_TTL_SECONDS:
            logger.debug("Market refresh skipped — cache is still fresh")
            return len(_cache["indices"]) + len(_cache["stocks"])

    index_results, stock_results = [], []
    async with httpx.AsyncClient(headers=_HEADERS) as client:
        for i, (symbol, label, unit, category) in enumerate(INDICES):
            if i > 0:
                await asyncio.sleep(0.5)
            item = await _fetch_one(client, symbol, label, unit, category)
            if item:
                index_results.append(item)
        for symbol, label, unit, category in BELLWETHER_STOCKS:
            await asyncio.sleep(0.5)
            item = await _fetch_one(client, symbol, label, unit, category)
            if item:
                stock_results.append(item)

    with _lock:
        if index_results or stock_results:
            _cache["indices"] = index_results
            _cache["stocks"] = stock_results
            _cache["cached_at"] = time.time()
            total_ok = len(index_results) + len(stock_results)
            total_expected = len(ALL_SYMBOLS)
            _cache["last_error"] = None if total_ok == total_expected else f"only {total_ok}/{total_expected} markets refreshed this cycle"
        else:
            _cache["last_error"] = "all market fetches failed this cycle"

    logger.info(f"Markets: {len(index_results)}/{len(INDICES)} indices, {len(stock_results)}/{len(BELLWETHER_STOCKS)} stocks refreshed")
    return len(index_results) + len(stock_results)


def get_markets() -> Dict[str, Any]:
    with _lock:
        return {
            "indices": list(_cache["indices"]),
            "stocks": list(_cache["stocks"]),
            "cached_at": _cache["cached_at"],
            "last_error": _cache["last_error"],
        }
