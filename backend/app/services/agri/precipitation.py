"""
precipitation.py — rainfall context for an AOI, layered against the risk
score the same way groundwater.py is: informative regional context, NOT a
scored input. It has a different temporal cadence (a rolling recent window
compared against a multi-year seasonal-normal baseline) than the 3-month
satellite indicators the composite risk score is built from, so it is
reported separately rather than folded into the same weighted average.

Uses CHIRPS (UCSB-CHG/CHIRPS/DAILY) — a widely-used global daily rainfall
estimate blending satellite and station data, ~5.5km resolution. Good for
a regional rainfall-anomaly read, not a rain-gauge-precise number for one
exact field.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import ee

from ..gee_client import _polygon_geometry

logger = logging.getLogger(__name__)

CHIRPS_SCALE_M = 5566  # native ~0.05° grid
# GEE's Python client calls block on network I/O and release the GIL while
# waiting, so a small thread pool gives real wall-clock concurrency here —
# not fake threading. Capped moderately (not one thread per year) to avoid
# hammering the GEE API with a burst of ~11 simultaneous requests from one
# report.
_MAX_CONCURRENT_WINDOWS = 5


def _window_total_mm(chirps: "ee.ImageCollection", region: "ee.Geometry", start: str, end: str) -> Optional[float]:
    col = chirps.filterDate(start, end)
    if col.size().getInfo() == 0:
        return None
    val = col.sum().reduceRegion(
        reducer=ee.Reducer.mean(), geometry=region, scale=CHIRPS_SCALE_M,
        maxPixels=1e9, bestEffort=True, tileScale=4,
    ).getInfo()
    return val.get("precipitation")


def compute_precipitation_context(
    aoi: Dict[str, Any], as_of: Optional[str] = None,
    recent_days: int = 90, years_back: int = 10,
) -> Dict[str, Any]:
    """Recent rainfall total for the AOI vs. a same-calendar-window
    historical average, expressed as an anomaly. Reports 'no_data' /
    'ok_no_baseline' rather than guessing when coverage or history is
    insufficient — same no-false-precision principle as elsewhere.

    Fetches the recent window and every historical year CONCURRENTLY via a
    thread pool rather than one at a time — the previous sequential version
    issued up to ~22 blocking round-trips (2 per window × up to 11 windows)
    back to back, a measured contributor to agri-report timeouts. Each
    window is still one call, but they now happen in parallel instead of
    queued behind each other."""
    region = _polygon_geometry(aoi)
    end_dt = datetime.strptime(as_of, "%Y-%m-%d") if as_of else datetime.utcnow()
    recent_start = end_dt - timedelta(days=recent_days)

    chirps = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY").filterBounds(region)

    # Build every window's (start, end) date pair upfront, then fetch them
    # all concurrently — key 0 is the recent window, 1..years_back are the
    # historical comparison years.
    windows: Dict[int, tuple] = {
        0: (recent_start.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")),
    }
    for y in range(1, years_back + 1):
        try:
            hist_end = end_dt.replace(year=end_dt.year - y)
        except ValueError:
            hist_end = end_dt.replace(year=end_dt.year - y, day=28)  # Feb 29 in a non-leap year
        hist_start = hist_end - timedelta(days=recent_days)
        windows[y] = (hist_start.strftime("%Y-%m-%d"), hist_end.strftime("%Y-%m-%d"))

    def _fetch(key: int) -> tuple:
        start, end = windows[key]
        try:
            return key, _window_total_mm(chirps, region, start, end)
        except Exception as e:
            logger.warning(f"precipitation context: window {key} fetch failed: {e}")
            return key, None

    with ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_WINDOWS) as pool:
        fetched = dict(pool.map(_fetch, windows.keys()))

    recent_total = fetched[0]
    if recent_total is None:
        return {
            "status": "no_data",
            "note": "CHIRPS rainfall coverage unavailable for this AOI/period.",
        }

    historical_totals = [fetched[y] for y in range(1, years_back + 1) if fetched.get(y) is not None]

    if len(historical_totals) < 3:
        return {
            "status": "ok_no_baseline",
            "recent_total_mm": round(recent_total, 1),
            "recent_days": recent_days,
            "note": "Not enough historical years of CHIRPS coverage to establish a seasonal-normal "
                    "baseline for comparison at this location.",
            "resolution_note": "CHIRPS ~5.5km grid — regional rainfall estimate, not a rain-gauge-precise "
                                "reading for one exact field.",
            "source": "UCSB-CHG/CHIRPS/DAILY",
        }

    hist_mean = sum(historical_totals) / len(historical_totals)
    anomaly_pct = round((recent_total - hist_mean) / hist_mean * 100, 1) if hist_mean > 0 else None

    if anomaly_pct is None:
        condition = "unknown"
    elif anomaly_pct <= -25:
        condition = "well below normal"
    elif anomaly_pct <= -10:
        condition = "below normal"
    elif anomaly_pct < 10:
        condition = "near normal"
    elif anomaly_pct < 25:
        condition = "above normal"
    else:
        condition = "well above normal"

    return {
        "status": "ok",
        "recent_total_mm": round(recent_total, 1),
        "recent_days": recent_days,
        "historical_mean_mm": round(hist_mean, 1),
        "years_used": len(historical_totals),
        "anomaly_pct": anomaly_pct,
        "condition": condition,
        "resolution_note": "CHIRPS ~5.5km grid — regional rainfall estimate, not a rain-gauge-precise "
                            "reading for one exact field.",
        "source": "UCSB-CHG/CHIRPS/DAILY",
    }
