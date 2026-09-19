"""
chokepoint_market_correlation.py — connects two signals that were
already being collected separately but never joined: business_risk.py's
chokepoint traffic-anomaly detection (backed by timeseries_store.py's
15-minute vessel-count snapshots) and market_indices.py's curated
bellwether equities (energy majors, shipping/logistics, defense primes).

The question this answers: on days when a chokepoint's vessel traffic
looked genuinely anomalous, did the bellwether stock(s) tied to that
chokepoint actually move? This is deliberately NOT a correlation
coefficient or any other statistic that implies more rigor than a
handful of daily samples can support — with typically 2-4 weeks of
15-minute snapshots behind it, there usually aren't enough independent
anomaly days for a coefficient to mean anything. Instead this reports,
plainly, what happened to the stock price on and after the highest-
anomaly days — the reader draws their own conclusion about whether
that's a real pattern or noise.

CHOKEPOINT → BELLWETHER mapping (stated explicitly, not left implicit):
Reuses the same energy-vs-shipping split business_risk.py's sibling
module, supply_chain.py, already uses for CHOKEPOINT_COMMODITIES (an
oil/LNG chokepoint watches energy futures there; a general shipping
lane watches grain/copper instead) — same reasoning, applied to
equities instead of futures:
  - strait_of_hormuz, bab_el_mandeb, suez_canal, strait_of_gibraltar:
    all primarily oil/LNG tanker routes (Hormuz/Bab-el-Mandeb/Suez
    directly; Gibraltar as the Mediterranean-Atlantic link many of the
    same tankers pass through) → energy majors (XOM, CVX,
    RELIANCE.NS) plus the shipping bellwethers, since general cargo
    also transits these
  - strait_of_malacca, panama_canal, english_channel: general
    high-volume shipping lanes without Hormuz/Suez's oil-flow
    concentration → shipping/logistics bellwethers only (ZIM, FDX)
"""

import logging
import math
from datetime import datetime, timezone
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional

from . import timeseries_store
from . import market_indices
from .supply_chain import CHOKEPOINT_DISPLAY
from .vessel_store import CHOKEPOINTS

logger = logging.getLogger(__name__)

_ENERGY_BELLWETHERS = ["XOM", "CVX", "RELIANCE.NS"]
_SHIPPING_BELLWETHERS = ["ZIM", "FDX"]

CHOKEPOINT_BELLWETHERS: Dict[str, List[str]] = {
    "strait_of_hormuz":    _ENERGY_BELLWETHERS + _SHIPPING_BELLWETHERS,
    "bab_el_mandeb":       _ENERGY_BELLWETHERS + _SHIPPING_BELLWETHERS,
    "suez_canal":          _ENERGY_BELLWETHERS + _SHIPPING_BELLWETHERS,
    "strait_of_gibraltar": _ENERGY_BELLWETHERS + _SHIPPING_BELLWETHERS,
    "strait_of_malacca":   _SHIPPING_BELLWETHERS,
    "panama_canal":        _SHIPPING_BELLWETHERS,
    "english_channel":     _SHIPPING_BELLWETHERS,
}

# Below this many distinct trading-relevant days of aggregated vessel
# data, ranking "highest-anomaly days" isn't meaningful — same spirit as
# timeseries_store.MIN_SAMPLES_FOR_BASELINE, applied at the daily
# aggregation level rather than the raw-sample level.
MIN_DAYS_FOR_ANALYSIS = 5

# How many top-anomaly days to report stock moves for. Matches the
# scope discussed for this feature (a small, readable handful of days,
# not a dump of every day in the window).
TOP_N_DAYS = 3


def _daily_means(raw_points: List[dict]) -> Dict[str, float]:
    """Buckets timeseries_store's 15-min samples (ISO timestamps) into
    one mean vessel count per UTC calendar date."""
    buckets: Dict[str, List[float]] = {}
    for p in raw_points:
        try:
            date_str = p["date"][:10]  # ISO 'YYYY-MM-DDTHH:MM:SS...' -> 'YYYY-MM-DD'
            buckets.setdefault(date_str, []).append(float(p["value"]))
        except (KeyError, ValueError, TypeError):
            continue
    return {d: mean(vals) for d, vals in buckets.items()}


def _rank_anomaly_days(daily: Dict[str, float]) -> List[dict]:
    """Z-score each day against the mean/stddev of the whole window
    (not a rolling baseline — with only 1-4 weeks of data here, the
    'window' already IS the baseline period; there isn't enough history
    to hold out a separate pre-period). Returns days sorted by |z|,
    highest first."""
    dates = sorted(daily)
    values = [daily[d] for d in dates]
    window_mean = mean(values)
    window_stddev = pstdev(values) if len(values) > 1 else 0.0

    scored = []
    for d, v in zip(dates, values):
        z = (v - window_mean) / window_stddev if window_stddev > 0.5 else 0.0
        scored.append({"date": d, "daily_mean_vessel_count": round(v, 1), "z_score": round(z, 2)})
    scored.sort(key=lambda s: abs(s["z_score"]), reverse=True)
    return scored


def _stock_daily_closes(points: List[dict]) -> Dict[str, float]:
    """market_indices.get_history() returns unix-second timestamps —
    bucket to UTC calendar dates the same way as vessel data so the two
    series can be matched by date string."""
    out: Dict[str, float] = {}
    for p in points:
        try:
            date_str = datetime.fromtimestamp(p["date"], tz=timezone.utc).strftime("%Y-%m-%d")
            out[date_str] = float(p["value"])
        except (KeyError, ValueError, TypeError, OSError):
            continue
    return out


def _pct_change(prev: Optional[float], curr: Optional[float]) -> Optional[float]:
    if prev is None or curr is None or prev == 0:
        return None
    return round((curr - prev) / prev * 100, 2)


def _moves_for_day(anomaly_date: str, closes: Dict[str, float]) -> dict:
    """Same-day move: anomaly_date's close vs the prior trading day's
    close (whichever is the closest earlier date actually present —
    Yahoo's daily series only has trading days, so weekends/holidays
    are naturally skipped rather than requiring calendar-aware logic
    here). Next-day move: the next trading day present after
    anomaly_date vs anomaly_date's own close."""
    trading_dates = sorted(closes)
    if anomaly_date not in closes:
        # No trading day exactly on the anomaly date (weekend/holiday) —
        # fall back to the nearest trading day at or after it, since
        # that's the first point the market could actually have reacted.
        later = [d for d in trading_dates if d >= anomaly_date]
        if not later:
            return {"same_day_change_pct": None, "next_day_change_pct": None, "note": "no market data at or after this date"}
        anomaly_date = later[0]

    idx = trading_dates.index(anomaly_date)
    prev_close = closes[trading_dates[idx - 1]] if idx > 0 else None
    curr_close = closes[anomaly_date]
    next_close = closes[trading_dates[idx + 1]] if idx + 1 < len(trading_dates) else None

    return {
        "same_day_change_pct": _pct_change(prev_close, curr_close),
        "next_day_change_pct": _pct_change(curr_close, next_close),
        "note": None,
    }


async def correlate_chokepoint_with_markets(cp_id: str, days: int = 28) -> Dict[str, Any]:
    if cp_id not in CHOKEPOINTS:
        return {"chokepoint": cp_id, "status": "unknown_chokepoint", "top_anomaly_days": []}

    bellwethers = CHOKEPOINT_BELLWETHERS.get(cp_id, [])
    raw_points = timeseries_store.get_chokepoint_history(cp_id, days=days)
    daily = _daily_means(raw_points)

    if len(daily) < MIN_DAYS_FOR_ANALYSIS:
        return {
            "chokepoint": cp_id,
            "chokepoint_display": CHOKEPOINT_DISPLAY.get(cp_id, cp_id),
            "bellwethers": bellwethers,
            "days_of_data": len(daily),
            "status": "insufficient_history",
            "top_anomaly_days": [],
        }

    ranked_days = _rank_anomaly_days(daily)
    top_days = ranked_days[:TOP_N_DAYS]

    # Fetch each bellwether's daily closes once (not once per top day) —
    # market_indices.get_history is an outbound HTTP call per symbol,
    # so this keeps the request count to len(bellwethers) regardless of
    # TOP_N_DAYS. range="3mo" covers this module's realistic `days`
    # inputs (timeseries_store itself only retains ~2x
    # BASELINE_LOOKBACK_DAYS = 28 days of raw snapshots).
    closes_by_symbol: Dict[str, Dict[str, float]] = {}
    for symbol in bellwethers:
        hist = await market_indices.get_history(symbol, range_="3mo")
        closes_by_symbol[symbol] = _stock_daily_closes(hist.get("points", []))

    symbol_names = {sym: label for sym, label, _unit, _cat in market_indices.BELLWETHER_STOCKS}

    out_days = []
    for day in top_days:
        stock_moves = []
        for symbol in bellwethers:
            moves = _moves_for_day(day["date"], closes_by_symbol.get(symbol, {}))
            stock_moves.append({"symbol": symbol, "name": symbol_names.get(symbol, symbol), **moves})
        out_days.append({**day, "stock_moves": stock_moves})

    return {
        "chokepoint": cp_id,
        "chokepoint_display": CHOKEPOINT_DISPLAY.get(cp_id, cp_id),
        "bellwethers": bellwethers,
        "days_of_data": len(daily),
        "status": "ok",
        "top_anomaly_days": out_days,
        "methodology_note": (
            "Z-scores are computed against this window's own mean/stddev "
            "(there isn't enough retained history for a separate baseline "
            "period). Same-day change compares each day's close to the "
            "prior trading day's; next-day change compares it to the "
            "following trading day's. This is a small-sample observation, "
            "not a statistically validated correlation."
        ),
    }
