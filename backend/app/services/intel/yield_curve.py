"""yield_curve.py — the US Treasury yield curve and its key spreads, from FRED's keyless CSV export
(https://fred.stlouisfed.org/graph/fredgraph.csv?id=SERIES), which serves the Federal Reserve's H.15 constant-maturity
series without an API key. Daily data, so cached for hours; a failed refresh keeps the last good curve and says so.

Descriptive only: the curve, its shape one month and one year ago, and the 10Y-2Y and 10Y-3M spreads with an `inverted`
flag (spread below zero). No recession probability or other forecast is computed.
"""
import asyncio
import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .data_quality import describe

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 6 * 60 * 60
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
SOURCE = "FRED (Board of Governors H.15 Treasury constant-maturity yields)"
TENORS = [("1M", "DGS1MO"), ("3M", "DGS3MO"), ("6M", "DGS6MO"), ("1Y", "DGS1"), ("2Y", "DGS2"), ("3Y", "DGS3"),
          ("5Y", "DGS5"), ("7Y", "DGS7"), ("10Y", "DGS10"), ("20Y", "DGS20"), ("30Y", "DGS30")]
HISTORY_DAYS = 400
_cache: Dict[str, Any] = {"data": None, "at": 0.0, "error": None}
_lock = threading.Lock()


def parse_fred_csv(text: str) -> List[Tuple[str, float]]:
    """[(YYYY-MM-DD, value)] from a fredgraph CSV; skips the header and missing observations ('.' or empty)."""
    out: List[Tuple[str, float]] = []
    for line in text.splitlines()[1:]:
        parts = line.strip().split(",")
        if len(parts) < 2 or parts[1] in ("", "."):
            continue
        try:
            datetime.strptime(parts[0], "%Y-%m-%d")
            out.append((parts[0], float(parts[1])))
        except ValueError:
            continue
    return out


def _value_on_or_before(series: List[Tuple[str, float]], target: date, max_gap_days: int = 7) -> Optional[Tuple[str, float]]:
    best = None
    for d, v in series:
        dd = date.fromisoformat(d)
        if dd <= target:
            best = (d, v)
        else:
            break
    if best and (target - date.fromisoformat(best[0])).days <= max_gap_days:
        return best
    return None


def build_curves(series: Dict[str, List[Tuple[str, float]]]) -> Optional[Dict[str, Any]]:
    """Pure function: per-tenor series -> curves (latest / 1 month ago / 1 year ago), spreads, and the 10Y-2Y history."""
    last_dates = [s[-1][0] for s in series.values() if s]
    if not last_dates:
        return None
    as_of = max(last_dates)
    as_of_d = date.fromisoformat(as_of)

    def curve_at(target: date) -> Dict[str, Optional[float]]:
        out = {}
        for tenor, _sid in TENORS:
            hit = _value_on_or_before(series.get(tenor, []), target)
            out[tenor] = hit[1] if hit else None
        return out

    latest, m1, y1 = curve_at(as_of_d), curve_at(as_of_d - timedelta(days=30)), curve_at(as_of_d - timedelta(days=365))

    def spread(c, a, b):
        return round(c[a] - c[b], 2) if c.get(a) is not None and c.get(b) is not None else None

    s_10_2, s_10_3m = spread(latest, "10Y", "2Y"), spread(latest, "10Y", "3M")
    two = dict(series.get("2Y", []))
    hist = [{"date": d, "value": round(v - two[d], 2)} for d, v in series.get("10Y", []) if d in two]
    return {
        "as_of": as_of, "tenors": [t for t, _ in TENORS],
        "curves": {"latest": latest, "one_month_ago": m1, "one_year_ago": y1},
        "spreads": {"10y_minus_2y": {"value": s_10_2, "inverted": (s_10_2 < 0) if s_10_2 is not None else None},
                    "10y_minus_3m": {"value": s_10_3m, "inverted": (s_10_3m < 0) if s_10_3m is not None else None}},
        "spread_10y_2y_history": hist,
    }


async def _fetch_series(client: httpx.AsyncClient, series_id: str, start: str) -> str:
    """The only network call in this module (tests replace it)."""
    r = await client.get(FRED_CSV_URL, params={"id": series_id, "cosd": start}, timeout=30)
    r.raise_for_status()
    return r.text


async def get_yield_curve(force: bool = False) -> Dict[str, Any]:
    with _lock:
        if not force and _cache["data"] and (time.time() - _cache["at"]) < CACHE_TTL_SECONDS:
            return {**_cache["data"], "data_quality": describe(_cache["at"], CACHE_TTL_SECONDS, _cache["error"], SOURCE, "Daily end-of-day data, published with a delay of about a day.")}
    start = (date.today() - timedelta(days=HISTORY_DAYS)).isoformat()
    sem = asyncio.Semaphore(4)

    async def one(client, tenor, sid):
        async with sem:
            try:
                rows = parse_fred_csv(await _fetch_series(client, sid, start))
                return tenor, [(d, v) for d, v in rows if d >= start]
            except Exception as e:
                logger.warning(f"yield curve fetch failed for {sid}: {type(e).__name__}")
                return tenor, []

    async with httpx.AsyncClient(headers={"User-Agent": "VAYU/1.0 (+https://github.com/RohitThanvi/Vayu)"}) as client:
        series = dict(await asyncio.gather(*(one(client, t, s) for t, s in TENORS)))
    built = build_curves(series)
    ok = sum(1 for s in series.values() if s)
    with _lock:
        if built and ok:
            _cache["data"] = {**built, "source": SOURCE}
            _cache["at"], _cache["error"] = time.time(), (None if ok == len(TENORS) else f"only {ok}/{len(TENORS)} tenors available this cycle")
        else:
            _cache["error"] = "yield curve unavailable this cycle"
        data, at, err = _cache["data"], _cache["at"], _cache["error"]
    if not data:
        return {"error": err, "data_quality": describe(0.0, CACHE_TTL_SECONDS, err, SOURCE)}
    return {**data, "data_quality": describe(at, CACHE_TTL_SECONDS, err, SOURCE, "Daily end-of-day data, published with a delay of about a day.")}
