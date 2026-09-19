"""
phenology.py — crop-stage (green-up / peak / senescence) detection from an
NDVI seasonal curve for an AOI.

This is the gap risk_scoring.py's own docstring names directly: "scoring
itself is crop-agnostic... a future version could add crop-specific
thresholds." This doesn't do crop-specific thresholds (that needs a known
crop calendar per crop type, a bigger project), but it does the more
general, still-real version: tell the caller WHERE in its own growing
season an AOI currently sits, from the shape of its own NDVI curve — so
"drought stress" can eventually be read differently near sowing than near
harvest, without hardcoding any particular crop's calendar.

Deliberately reuses gee_remote_sensing.compute_index_time_series rather
than re-deriving a Sentinel-2 NDVI composite here — same DRY reasoning
applied throughout this codebase (risk_scoring.py reusing gee_client
functions, the Spectra ML tool reusing _index_image).

Method is a simple, documented heuristic — NOT a validated phenology
product (see e.g. MODIS MCD12Q2, which uses smoothed daily/8-day curves
and per-pixel logistic curve fitting): a relative-amplitude threshold
crossing on a monthly-composite NDVI curve. Monthly composites can miss a
growth stage that starts and ends within a single month, and Sentinel-2
cloud gaps can hide the true seasonal min/max — both are stated plainly
in the output rather than glossed over, same no-false-precision principle
used throughout the rest of this project (groundwater's resolution_note,
risk_scoring's confidence field, etc.).
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from ..gee_remote_sensing import compute_index_time_series

logger = logging.getLogger(__name__)

# How far up from the season's own min-to-max range a point has to rise
# (on the way up) / fall (on the way down) to count as "in season" —
# a commonly-used relative-amplitude choice for green-up/senescence
# thresholds in NDVI phenology literature, not a project-specific guess.
_STAGE_THRESHOLD_FRACTION = 0.4
MIN_VALID_POINTS = 4


def compute_phenology(aoi: Dict[str, Any], as_of: Optional[str] = None, months_back: int = 12) -> Dict[str, Any]:
    """
    Fetches a monthly NDVI time series for the `months_back` months up to
    `as_of` (default: today), finds the seasonal min/max and their dates,
    and reports where `as_of` currently falls relative to green-up/peak/
    senescence — plus the detected dates themselves, so a caller isn't
    just handed a label with no evidence behind it.
    """
    as_of_dt = datetime.strptime(as_of, "%Y-%m-%d") if as_of else datetime.utcnow()
    as_of_str = as_of_dt.strftime("%Y-%m-%d")
    start_dt = as_of_dt - timedelta(days=30 * months_back)
    start_str = start_dt.strftime("%Y-%m-%d")

    logger.info(f"agri phenology: {start_str} -> {as_of_str}")
    series = compute_index_time_series(aoi, "ndvi", start_str, as_of_str, interval="month")
    points = [p for p in series["points"] if p["value"] is not None]

    if len(points) < MIN_VALID_POINTS:
        return {
            "status": "insufficient_data",
            "points_used": len(points),
            "note": f"Only {len(points)} cloud-free monthly NDVI readings in the last {months_back} months "
                    f"— not enough to characterize a growing-season curve for this AOI/period.",
            "series": series["points"],
        }

    values = [p["value"] for p in points]
    dates = [p["date"] for p in points]
    v_min, v_max = min(values), max(values)
    peak_idx = values.index(v_max)
    peak_date = dates[peak_idx]

    if v_max - v_min < 0.05:
        # Curve is essentially flat (e.g. perennial vegetation, or a
        # non-agricultural AOI) — reporting green-up/senescence dates off
        # a near-zero amplitude would be noise dressed up as a finding.
        return {
            "status": "flat_curve",
            "seasonal_min_ndvi": round(v_min, 4), "seasonal_max_ndvi": round(v_max, 4),
            "note": "NDVI barely varies across this window (range < 0.05) — no clear seasonal cycle "
                    "detected, so no growth stages are being called out. This can be normal (perennial "
                    "vegetation, a mostly non-vegetated AOI) rather than a data problem.",
            "series": series["points"],
        }

    threshold = v_min + _STAGE_THRESHOLD_FRACTION * (v_max - v_min)

    green_up_date = None
    for i in range(peak_idx, -1, -1):
        if values[i] < threshold:
            green_up_date = dates[i + 1] if i + 1 <= peak_idx else dates[peak_idx]
            break
    else:
        green_up_date = dates[0]  # curve was already above threshold at window start — genuinely unknown when it started

    senescence_date = None
    for i in range(peak_idx, len(values)):
        if values[i] < threshold:
            senescence_date = dates[i]
            break

    if as_of_str < green_up_date:
        current_stage = "pre-season / fallow"
    elif as_of_str < peak_date:
        current_stage = "vegetative growth (green-up)"
    elif senescence_date is None or as_of_str <= peak_date:
        current_stage = "peak greenness"
    elif as_of_str < senescence_date:
        current_stage = "maturity / early senescence"
    else:
        current_stage = "senescence / post-harvest"

    return {
        "status": "ok",
        "current_stage": current_stage,
        "as_of": as_of_str,
        "seasonal_min_ndvi": round(v_min, 4),
        "seasonal_max_ndvi": round(v_max, 4),
        "peak_date": peak_date,
        "green_up_date": green_up_date,
        "senescence_date": senescence_date,  # null if the curve hadn't fallen back below threshold within the window yet
        "points_used": len(points),
        "series": series["points"],
        "method": (
            f"Monthly Sentinel-2 NDVI composite, {start_str} to {as_of_str}. Green-up/senescence dates are "
            f"the first month crossing {int(_STAGE_THRESHOLD_FRACTION*100)}% of this AOI's own seasonal "
            f"min-to-max NDVI range ({round(v_min,3)}-{round(v_max,3)}) on the rising/falling side of the "
            f"peak — a heuristic on monthly composites, not a validated phenology product; a stage that "
            f"starts and ends within one month can be missed."
        ),
    }
