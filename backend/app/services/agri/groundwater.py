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
from datetime import datetime, timedelta, timezone
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

    # Extract the time series with ONE network round-trip via getRegion(),
    # not one per image. The previous version looped size (up to ~60 for a
    # 5-year window) times, issuing 2 blocking .getInfo() calls per
    # iteration (~120 sequential round-trips total) — a major, measured
    # contributor to agri report timeouts. getRegion() is GEE's own
    # purpose-built API for exactly this (extract a region's value across
    # every image in a collection) and returns every row in a single call.
    centroid = region.centroid(maxError=100)
    # Sampling the centroid rather than reducing the full polygon: GRACE's
    # ~300km grid means a Vayu-scale AOI (city/district) almost always
    # falls entirely within a single pixel anyway, so this is numerically
    # equivalent to a region-mean in practice — and it avoids getRegion()
    # returning multiple per-pixel rows for the same date if the AOI ever
    # did straddle a cell boundary, which would otherwise need extra
    # same-date averaging logic to not corrupt the trend fit below.
    try:
        rows = col.getRegion(centroid, scale=100000).getInfo()
    except Exception as e:
        logger.warning(f"groundwater trend: getRegion failed ({e}), falling back to per-image loop")
        rows = None

    series = []
    if rows is not None and len(rows) > 1:
        header = rows[0]
        try:
            time_idx = header.index("time")
            val_idx = header.index("lwe_thickness_csr")
        except ValueError:
            rows = None  # unexpected header shape — trigger the fallback below
        else:
            for row in rows[1:]:
                lwe = row[val_idx]
                if lwe is not None:
                    date_str = datetime.fromtimestamp(row[time_idx] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                    series.append((date_str, lwe))

    if rows is None:
        # Fallback: the original slower-but-reliable per-image approach,
        # only reached if getRegion() itself failed outright.
        img_list = col.toList(size)
        for i in range(size):
            img = ee.Image(img_list.get(i))
            date = img.date().format("YYYY-MM-dd").getInfo()
            val = img.reduceRegion(reducer=ee.Reducer.mean(), geometry=region, scale=100000, maxPixels=1e9).getInfo()
            lwe = val.get("lwe_thickness_csr")
            if lwe is not None:
                series.append((date, lwe))

    series.sort(key=lambda pair: pair[0])

    if len(series) < 2:
        return {"status": "insufficient_data", "points": len(series),
                "note": f"Only {len(series)} valid monthly GRACE readings found for this AOI/period \u2014 "
                        f"not enough to fit a reliable trend line."}

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
