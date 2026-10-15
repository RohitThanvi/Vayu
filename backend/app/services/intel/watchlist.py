"""watchlist.py — on-demand quotes and history for ANY Yahoo symbol, so a user can build a real watchlist.

The curated lists in market_indices.py / commodity_prices.py are refreshed on a schedule. A watchlist can hold arbitrary
tickers, so these are fetched on demand — which makes this the one place user input reaches an upstream URL, so it is
deliberately defensive:
  - symbols are validated against a strict pattern and normalised before they go anywhere near a request;
  - at most MAX_SYMBOLS_PER_REQUEST symbols per call, fetched with bounded concurrency;
  - results are cached (quotes 5 min, history 30 min) in a size-capped cache, so repeated or abusive requests for the same
    symbols cost one upstream call, and the number of distinct cached symbols can't grow without bound;
  - one bad/unknown symbol never fails the others, and raw upstream error text is never echoed to the client.
Same unofficial Yahoo chart endpoint (and the same honest caveats) as the rest of the Business tab.
"""
import asyncio
import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .commodity_prices import YAHOO_CHART_URL, _HEADERS
from .data_quality import describe

logger = logging.getLogger(__name__)

SYMBOL_RE = re.compile(r"^[A-Za-z0-9.^=\-]{1,15}$")
MAX_SYMBOLS_PER_REQUEST = 12
QUOTE_TTL_SECONDS = 5 * 60
HISTORY_TTL_SECONDS = 30 * 60
MAX_CACHE_ENTRIES = 400
VALID_RANGES = {"1mo", "3mo", "6mo", "1y", "2y", "5y"}
_FETCH_CONCURRENCY = 4

_quote_cache: "OrderedDict[str, Tuple[float, Dict[str, Any]]]" = OrderedDict()
_history_cache: "OrderedDict[Tuple[str, str], Tuple[float, Dict[str, Any]]]" = OrderedDict()
_lock = threading.Lock()


def normalize_symbol(raw: Any) -> Optional[str]:
    """Upper-cased symbol if it is a plausible Yahoo ticker, else None."""
    if not isinstance(raw, str):
        return None
    s = raw.strip().upper()
    return s if SYMBOL_RE.match(s) else None


def _cache_get(cache: OrderedDict, key, ttl: int):
    with _lock:
        hit = cache.get(key)
        if hit and (time.time() - hit[0]) < ttl:
            cache.move_to_end(key)
            return hit
        return None


def _cache_put(cache: OrderedDict, key, value):
    with _lock:
        cache[key] = (time.time(), value)
        cache.move_to_end(key)
        while len(cache) > MAX_CACHE_ENTRIES:
            cache.popitem(last=False)


def clear_caches():
    with _lock:
        _quote_cache.clear()
        _history_cache.clear()


async def _fetch_chart(client: httpx.AsyncClient, symbol: str, range_: str, interval: str = "1d") -> Dict[str, Any]:
    """The only function here that touches the network (so tests replace exactly this)."""
    resp = await client.get(YAHOO_CHART_URL.format(symbol=symbol), params={"interval": interval, "range": range_}, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _num(meta: Dict[str, Any], key: str) -> Optional[float]:
    v = meta.get(key)
    return round(float(v), 4) if isinstance(v, (int, float)) else None


def parse_quote(data: Dict[str, Any], symbol: str) -> Optional[Dict[str, Any]]:
    """Quote dict from a Yahoo chart response, or None when it holds no usable price."""
    try:
        result = data["chart"]["result"][0]
        meta = result.get("meta", {})
    except (KeyError, IndexError, TypeError):
        return None
    price = meta.get("regularMarketPrice")
    prev = meta.get("previousClose") or meta.get("chartPreviousClose")
    if price is None:
        try:
            for c in reversed(result["indicators"]["quote"][0]["close"]):
                if c is not None:
                    price = c
                    break
        except (KeyError, IndexError, TypeError):
            pass
    if price is None:
        return None
    change_pct = None
    if prev:
        try:
            change_pct = round((float(price) - float(prev)) / float(prev) * 100, 2)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return {
        "symbol": symbol,
        "name": meta.get("shortName") or meta.get("longName") or symbol,
        "currency": meta.get("currency"),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "value": round(float(price), 4),
        "prev_close": round(float(prev), 4) if prev else None,
        "change_pct": change_pct,
        "day_high": _num(meta, "regularMarketDayHigh"),
        "day_low": _num(meta, "regularMarketDayLow"),
        "week52_high": _num(meta, "fiftyTwoWeekHigh"),
        "week52_low": _num(meta, "fiftyTwoWeekLow"),
        "market_time": meta.get("regularMarketTime"),
    }


def parse_history(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = data["chart"]["result"][0]
    return [{"date": ts, "value": round(float(c), 4)}
            for ts, c in zip(result.get("timestamp", []), result["indicators"]["quote"][0]["close"]) if c is not None]


async def get_quotes(symbols: List[str]) -> Dict[str, Any]:
    """Quotes for up to MAX_SYMBOLS_PER_REQUEST symbols, in request order. Raises ValueError on a bad request shape."""
    if not isinstance(symbols, list) or not symbols:
        raise ValueError("Provide at least one symbol.")
    cleaned: List[str] = []
    errors: Dict[str, str] = {}
    for raw in symbols:
        sym = normalize_symbol(raw)
        if sym is None:
            errors[str(raw)[:20]] = "invalid symbol"
        elif sym not in cleaned:
            cleaned.append(sym)
    if len(cleaned) > MAX_SYMBOLS_PER_REQUEST:
        raise ValueError(f"At most {MAX_SYMBOLS_PER_REQUEST} symbols per request.")

    results: Dict[str, Dict[str, Any]] = {}
    fetched_at: Dict[str, float] = {}
    to_fetch = []
    for sym in cleaned:
        hit = _cache_get(_quote_cache, sym, QUOTE_TTL_SECONDS)
        if hit:
            fetched_at[sym], results[sym] = hit[0], hit[1]
        else:
            to_fetch.append(sym)

    if to_fetch:
        sem = asyncio.Semaphore(_FETCH_CONCURRENCY)

        async def one(client, sym):
            async with sem:
                try:
                    q = parse_quote(await _fetch_chart(client, sym, "5d"), sym)
                except Exception as e:  # never echo upstream details to the client
                    logger.warning(f"watchlist quote fetch failed for {sym}: {type(e).__name__}")
                    return sym, None
                return sym, q

        async with httpx.AsyncClient(headers=_HEADERS) as client:
            for sym, q in await asyncio.gather(*(one(client, s) for s in to_fetch)):
                if q is None:
                    errors[sym] = "no quote available (unknown symbol or upstream unavailable)"
                else:
                    _cache_put(_quote_cache, sym, q)
                    results[sym], fetched_at[sym] = q, time.time()

    quotes = [results[s] for s in cleaned if s in results]
    oldest = min((fetched_at[s] for s in cleaned if s in fetched_at), default=None)
    return {"quotes": quotes, "errors": errors, "data_quality": describe(oldest, QUOTE_TTL_SECONDS, None)}


async def get_history(symbol: str, range_: str = "1y") -> Dict[str, Any]:
    sym = normalize_symbol(symbol)
    if sym is None:
        raise ValueError("Invalid symbol.")
    if range_ not in VALID_RANGES:
        range_ = "1y"
    hit = _cache_get(_history_cache, (sym, range_), HISTORY_TTL_SECONDS)
    if hit:
        return hit[1]
    try:
        async with httpx.AsyncClient(headers=_HEADERS) as client:
            data = await _fetch_chart(client, sym, range_)
        points = parse_history(data)
        name = (data["chart"]["result"][0].get("meta", {}) or {}).get("shortName") or sym
    except Exception as e:
        logger.warning(f"watchlist history fetch failed for {sym}: {type(e).__name__}")
        return {"symbol": sym, "range": range_, "points": [], "error": "history unavailable (unknown symbol or upstream unavailable)"}
    out = {"symbol": sym, "name": name, "range": range_, "points": points}
    if points:
        _cache_put(_history_cache, (sym, range_), out)
    return out
