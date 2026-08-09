"""
groundwater.py — groundwater trend overlay, meant to be layered against the
risk score's moisture/drought signal (surface stress can be a groundwater-
depletion story, not just a rainfall one — worth surfacing together).

Uses GRACE/GRACE-FO terrestrial water storage anomaly via GEE, which is
coarse (~300km resolution) but global and free — good enough for a
regional trend line, not for parcel-level precision. Documented as such
here rather than overstating precision (same "no false precision" principle
as the risk score's confidence field).
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict

import ee

from ..gee_client import _polygon_geometry

logger = logging.getLogger(__name__)


def compute_groundwater_trend(aoi: Dict[str, Any], years_back: int = 5) -> Dict[str, Any]:
    region = _polygon_geometry(aoi)
    end_dt = datetime.utcnow()
    start_dt = end_dt - timedelta(days=365 * years_back)

    col = (
        ee.ImageCollection("NASA/GRACE/MASS_GRIDS_V04/LAND")
        .filterBounds(region)
        .filterDate(start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"))
        .select("lwe_thickness_csr")  # liquid water equivalent thickness anomaly, cm
    )

    size = col.size().getInfo()
    if size == 0:
        return {
            "status": "no_data",
            "note": "GRACE coverage unavailable for this AOI/period (coarse ~300km grid; small or "
                    "coastal AOIs sometimes fall outside a usable footprint).",
        }

    # Build a time series of monthly regional means to fit a trend
    img_list = col.toList(size)
    series = []
    for i in range(size):
        img = ee.Image(img_list.get(i))
        date = img.date().format("YYYY-MM-dd").getInfo()
        val = img.reduceRegion(reducer=ee.Reducer.mean(), geometry=region, scale=100000, maxPixels=1e9).getInfo()
        lwe = val.get("lwe_thickness_csr")
        if lwe is not None:
            series.append((date, lwe))

    if len(series) < 2:
        return {"status": "insufficient_data", "points": len(series)}

    # simple linear trend (least squares slope) over the series, in cm/year
    n = len(series)
    xs = list(range(n))
    ys = [v for _, v in series]
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    num = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
    den = sum((xs[i] - x_mean) ** 2 for i in range(n)) or 1
    slope_per_sample = num / den
    # samples are ~monthly
    slope_per_year = slope_per_sample * 12

    trend = "declining" if slope_per_year < -0.5 else "rising" if slope_per_year > 0.5 else "stable"

    return {
        "status": "ok",
        "trend": trend,
        "slope_cm_per_year": round(slope_per_year, 3),
        "latest_anomaly_cm": round(ys[-1], 3),
        "latest_date": series[-1][0],
        "points_used": n,
        "resolution_note": "GRACE/GRACE-FO ~300km grid — regional trend only, not parcel-level precision.",
        "source": "NASA/GRACE/MASS_GRIDS_V04/LAND (lwe_thickness_csr)",
    }
