"""
baseline.py — multi-year historical NDVI baseline and seasonal-normal
comparison for any AOI.

Answers "is this year's vegetation condition normal for this time of year
here" rather than just "what does it look like right now" — a condition
snapshot alone can't tell you if what you're seeing is a real anomaly.

Generalized: works for any polygon and any date range; "years_back" and
"window_days" are parameters, not hardcoded to a crop calendar.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict

import ee

from ..gee_client import _polygon_geometry, _mask_s2_clouds, _cap_end_date

logger = logging.getLogger(__name__)


def compute_seasonal_baseline(aoi: Dict[str, Any], as_of: str = None,
                               years_back: int = 5, window_days: int = 15) -> Dict[str, Any]:
    """
    Compares the current NDVI (averaged over a window_days window centered on
    `as_of`) against the mean and std-dev of NDVI in that same calendar window
    across the previous `years_back` years — a true seasonal-normal comparison,
    not a rolling year-over-year diff.
    """
    as_of = as_of or datetime.utcnow().strftime("%Y-%m-%d")
    as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")
    region = _polygon_geometry(aoi)

    def ndvi_mean_for_window(center_dt: datetime) -> float:
        start = ee.Date((center_dt - timedelta(days=window_days)).strftime("%Y-%m-%d"))
        end = _cap_end_date((center_dt + timedelta(days=window_days)).strftime("%Y-%m-%d"))
        col = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(region)
            .filterDate(start, end)
            .map(_mask_s2_clouds)
        )
        if col.size().getInfo() == 0:
            return None
        ndvi = col.map(lambda img: img.normalizedDifference(["B8", "B4"])).mean()
        stats = ndvi.reduceRegion(reducer=ee.Reducer.mean(), geometry=region, scale=30, maxPixels=1e9).getInfo()
        return stats.get("nd")

    current = ndvi_mean_for_window(as_of_dt)

    historical = []
    for y in range(1, years_back + 1):
        past_center = as_of_dt.replace(year=as_of_dt.year - y)
        val = ndvi_mean_for_window(past_center)
        if val is not None:
            historical.append(val)

    if not historical or current is None:
        return {
            "current_ndvi": current,
            "seasonal_normal_ndvi": None,
            "anomaly": None,
            "anomaly_pct": None,
            "years_used": len(historical),
            "status": "insufficient_data",
        }

    mean_hist = sum(historical) / len(historical)
    variance = sum((x - mean_hist) ** 2 for x in historical) / len(historical)
    std_hist = variance ** 0.5

    anomaly = current - mean_hist
    anomaly_pct = (anomaly / mean_hist * 100) if mean_hist else None
    z_score = (anomaly / std_hist) if std_hist > 0 else None

    if z_score is not None and z_score <= -1.5:
        status = "well_below_normal"
    elif z_score is not None and z_score <= -0.5:
        status = "below_normal"
    elif z_score is not None and z_score >= 1.5:
        status = "well_above_normal"
    elif z_score is not None and z_score >= 0.5:
        status = "above_normal"
    else:
        status = "normal"

    return {
        "current_ndvi": round(current, 4),
        "seasonal_normal_ndvi": round(mean_hist, 4),
        "seasonal_std_ndvi": round(std_hist, 4),
        "anomaly": round(anomaly, 4),
        "anomaly_pct": round(anomaly_pct, 2) if anomaly_pct is not None else None,
        "z_score": round(z_score, 2) if z_score is not None else None,
        "years_used": len(historical),
        "status": status,
        "as_of": as_of,
        "window_days": window_days,
    }
