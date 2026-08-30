"""
commodity_prices.py — a "market intelligence" layer for the ticker at the
bottom of the screen: global commodity prices (crude oil, natural gas,
metals, agri commodities), free and keyless-adjacent (needs a free
Alpha Vantage API key, no card).

Honest scope note: this is NOT MCX (India's commodity exchange) real-time
data — MCX's actual live feed is a paid exchange subscription with no
free/legal real-time alternative. This uses Alpha Vantage's free global
Commodities API instead, which is monthly-resolution for most of these
symbols (their docs note interval availability varies "depending on the
commodity" — energy has daily/weekly options, but not consistently across
all ten), not a live tick feed. That's fine for a slow-moving ticker
that's meant to show general market context, not a trading terminal.

Each commodity is its own API call (function=WTI, function=BRENT, etc.)
— ten calls per refresh — and the free tier's daily request budget is
low, so this refreshes once a day server-side and caches the result,
the same pattern used for the satellite-imagery layers.
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 24 * 60 * 60

ALPHAVANTAGE_URL = "https://www.alphavantage.co/query"

# (function code, display label, unit fallback if the API doesn't return one)
COMMODITIES = [
    ("WTI", "Crude Oil (WTI)", "USD/barrel"),
    ("BRENT", "Crude Oil (Brent)", "USD/barrel"),
    ("NATURAL_GAS", "Natural Gas", "USD/MMBtu"),
    ("COPPER", "Copper", "USD/metric ton"),
    ("ALUMINUM", "Aluminum", "USD/metric ton"),
    ("WHEAT", "Wheat", "USD/metric ton"),
    ("CORN", "Corn", "USD/metric ton"),
    ("COTTON", "Cotton", "USD/lb"),
    ("SUGAR", "Sugar", "USD/lb"),
    ("COFFEE", "Coffee", "USD/lb"),
]

_cache: Dict[str, Any] = {"commodities": [], "cached_at": 0.0, "last_error": None}
_lock = threading.Lock()


async def _fetch_one(client: httpx.AsyncClient, function: str, label: str, unit_fallback: str, api_key: str) -> Optional[Dict[str, Any]]:
    try:
        resp = await client.get(
            ALPHAVANTAGE_URL,
            params={"function": function, "interval": "monthly", "apikey": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"Commodity fetch failed for {function}: {type(e).__name__}: {e}")
        return None

    # Alpha Vantage returns a 200 with a "Note"/"Information" body instead
    # of a real error status when the daily rate limit is hit -- treat
    # that as a failure for this symbol rather than crashing on missing keys.
    series = data.get("data")
    if not series:
        note = data.get("Note") or data.get("Information") or data.get("Error Message")
        if note:
            logger.warning(f"Commodity fetch for {function} returned no data: {note}")
        return None

    latest = series[0]
    previous = series[1] if len(series) > 1 else None
    try:
        latest_value = float(latest["value"])
    except (KeyError, TypeError, ValueError):
        return None
    change_pct = None
    if previous is not None:
        try:
            prev_value = float(previous["value"])
            if prev_value != 0:
                change_pct = (latest_value - prev_value) / prev_value * 100
        except (KeyError, TypeError, ValueError):
            pass

    return {
        "symbol": function,
        "name": label,
        "unit": data.get("unit") or unit_fallback,
        "value": latest_value,
        "date": latest.get("date"),
        "change_pct": round(change_pct, 2) if change_pct is not None else None,
    }


async def refresh(api_key: str) -> int:
    """Fetch all configured commodities and refresh the cache. Returns the
    count that succeeded (a partial failure — e.g. hitting the daily rate
    limit partway through — still caches whatever succeeded rather than
    discarding it for an all-or-nothing refresh)."""
    if not api_key:
        with _lock:
            _cache["last_error"] = "ALPHAVANTAGE_API_KEY not configured"
        return 0

    results = []
    async with httpx.AsyncClient(headers={"User-Agent": "VAYU-Intelligence-Terminal/2.0"}) as client:
        for function, label, unit_fallback in COMMODITIES:
            item = await _fetch_one(client, function, label, unit_fallback, api_key)
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
