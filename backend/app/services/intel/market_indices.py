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
from .data_quality import describe as describe_quality, merge_quotes

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 15 * 60   # the quotes are themselves ~15-20 min delayed, so refreshing faster would add requests but no new information

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

# NSE sector indices (India). NOTE: these Yahoo symbols could not be live-checked from the build sandbox (no Yahoo access).
# A symbol Yahoo does not recognise simply yields no quote and is omitted from the heatmap — it never breaks a refresh —
# and the UI reports "n of N sectors available" so a missing tile is visible rather than silent.
SECTORS = [
    ("^NSEBANK", "Nifty Bank", "index", "india_sector"),
    ("^CNXIT", "Nifty IT", "index", "india_sector"),
    ("^CNXAUTO", "Nifty Auto", "index", "india_sector"),
    ("^CNXPHARMA", "Nifty Pharma", "index", "india_sector"),
    ("^CNXFMCG", "Nifty FMCG", "index", "india_sector"),
    ("^CNXMETAL", "Nifty Metal", "index", "india_sector"),
    ("^CNXENERGY", "Nifty Energy", "index", "india_sector"),
    ("^CNXREALTY", "Nifty Realty", "index", "india_sector"),
    ("^CNXINFRA", "Nifty Infrastructure", "index", "india_sector"),
    ("^CNXPSUBANK", "Nifty PSU Bank", "index", "india_sector"),
]

# Currency pairs and the dollar index (Yahoo quotes "INR=X" as USD/INR: rupees per dollar, so higher = weaker rupee).
FX = [
    ("INR=X", "USD/INR", "INR per USD", "fx"),
    ("EURUSD=X", "EUR/USD", "USD per EUR", "fx"),
    ("GBPUSD=X", "GBP/USD", "USD per GBP", "fx"),
    ("JPY=X", "USD/JPY", "JPY per USD", "fx"),
    ("DX-Y.NYB", "US Dollar Index", "index", "fx"),
]

GROUPS = (("indices", INDICES), ("stocks", BELLWETHER_STOCKS), ("sectors", SECTORS), ("fx", FX))
ALL_SYMBOLS = [row for _, rows in GROUPS for row in rows]

_cache: Dict[str, Any] = {"cached_at": 0.0, "last_error": None, **{g: [] for g, _ in GROUPS}}
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
    """Fetch every configured symbol and refresh the cache. A symbol that fails this cycle keeps its last good quote
    (flagged stale) rather than vanishing, and a partial failure still caches whatever succeeded."""
    if not force:
        with _lock:
            cached_at = _cache["cached_at"]
        if cached_at and (time.time() - cached_at) < CACHE_TTL_SECONDS:
            logger.debug("Market refresh skipped — cache is still fresh")
            return sum(len(_cache[g]) for g, _ in GROUPS)

    fresh: Dict[str, list] = {g: [] for g, _ in GROUPS}
    first = True
    async with httpx.AsyncClient(headers=_HEADERS) as client:
        for group, rows in GROUPS:
            for symbol, label, unit, category in rows:
                if not first:
                    await asyncio.sleep(0.5)
                first = False
                item = await _fetch_one(client, symbol, label, unit, category)
                if item:
                    fresh[group].append(item)

    total_ok = sum(len(v) for v in fresh.values())
    with _lock:
        if total_ok:
            for group, rows in GROUPS:
                _cache[group] = merge_quotes(_cache[group], fresh[group], rows)
            _cache["cached_at"] = time.time()
            expected = len(ALL_SYMBOLS)
            _cache["last_error"] = None if total_ok == expected else f"only {total_ok}/{expected} markets refreshed this cycle (the rest show their last good quote, flagged stale)"
        else:
            _cache["last_error"] = "all market fetches failed this cycle"

    logger.info("Markets: " + ", ".join(f"{len(fresh[g])}/{len(rows)} {g}" for g, rows in GROUPS) + " refreshed")
    return total_ok


def get_markets() -> Dict[str, Any]:
    with _lock:
        out = {g: list(_cache[g]) for g, _ in GROUPS}
        out["cached_at"] = _cache["cached_at"]
        out["last_error"] = _cache["last_error"]
        out["data_quality"] = describe_quality(_cache["cached_at"], CACHE_TTL_SECONDS, _cache["last_error"])
        return out
