"""
risk_scoring.py — composite agricultural risk score for any AOI.

This is the Tier-1 differentiator: instead of raw NDVI/soil-moisture/drought
numbers (a "condition snapshot"), this turns them into one 0-100 risk score
+ a confidence value + a plain-language reason — something a farmer or
officer can act on without interpreting satellite indices themselves.

Deliberately generalized:
- Works on ANY polygon (a hand-drawn AOI, a searched place boundary, a
  saved watchlist region) — no hardcoded district or crop.
- Crop is an optional label for display only; the scoring itself is
  crop-agnostic (vegetation stress / drought / moisture deficit apply to
  any planted land). A future version could add crop-specific thresholds,
  but a generalized baseline is more broadly useful today.

Built entirely on gee_client functions that already exist in this codebase
(compute_vegetation_change, compute_drought_index, compute_soil_moisture)
rather than reimplementing satellite analysis.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from ..gee_client import compute_vegetation_change, compute_drought_index, compute_soil_moisture
from . import db

logger = logging.getLogger(__name__)

# Weights for the composite score. Drought stress and vegetation loss matter
# most for "is this land in trouble right now"; moisture deficit compounds it.
WEIGHTS = {
    "drought": 0.40,
    "vegetation_loss": 0.35,
    "moisture_deficit": 0.25,
}


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _drought_subscore(drought_metrics: Dict[str, Any]) -> float:
    """0-100: share of the AOI under drought stress (NDDI > 0.5)."""
    affected = drought_metrics.get("drought_affected_pct")
    if affected is None:
        # fall back to computing from area fields if pct wasn't returned
        affected_km2 = drought_metrics.get("drought_affected_km2", 0) or 0
        # can't get pct without total area here; treat presence of any
        # affected area conservatively
        return _clamp(min(affected_km2, 50) * 2) if affected_km2 else 0.0
    return _clamp(affected * 100 if affected <= 1 else affected)


def _vegetation_subscore(veg_metrics: Dict[str, Any]) -> float:
    """0-100: vegetation loss as a share of the initial vegetated area."""
    loss_pct = veg_metrics.get("loss_pct", 0) or 0
    return _clamp(loss_pct)


def _moisture_subscore(moisture_metrics: Dict[str, Any]) -> Optional[float]:
    """0-100: how far soil moisture has dropped, and how dry the end state is.
    Returns None (not 0) when SMAP had no real coverage for this AOI/window —
    a missing reading must never look identical to a verified low-risk one."""
    if not moisture_metrics.get("data_available", True):
        return None
    change = moisture_metrics.get("moisture_change", 0) or 0
    dry_km2 = moisture_metrics.get("dry_stress_area_km2")
    if dry_km2 is None:
        return None
    drop_component = _clamp(max(0, -change) * 500)  # moisture is a small fraction (m3/m3)
    dry_component = _clamp(min(dry_km2, 50) * 2)
    return _clamp((drop_component + dry_component) / 2)


def compute_risk_score(aoi: Dict[str, Any], as_of: Optional[str] = None,
                        region_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Compute a composite 0-100 risk score for an AOI, comparing the last 3
    months against the prior period (matches the existing gee_client
    start/end-period convention used elsewhere in this codebase).

    Returns: score, band (low/moderate/high/severe), confidence, plain-
    language reason, and the underlying sub-metrics for transparency
    (explainability + audit trail, per the ground-truth/trust requirements).
    """
    end_date = as_of or datetime.utcnow().strftime("%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    start_date = (end_dt - timedelta(days=365)).strftime("%Y-%m-%d")

    errors = []
    error_details = {}
    veg_metrics, drought_metrics, moisture_metrics = {}, {}, {}

    try:
        veg = compute_vegetation_change(aoi=aoi, start_date=start_date, end_date=end_date)
        veg_metrics = veg["metrics"]
    except Exception as e:
        logger.warning(f"risk_scoring: vegetation_change failed: {e}", exc_info=True)
        errors.append("vegetation")
        error_details["vegetation"] = str(e)

    try:
        drought = compute_drought_index(aoi=aoi, start_date=start_date, end_date=end_date)
        drought_metrics = drought["metrics"]
    except Exception as e:
        logger.warning(f"risk_scoring: drought_index failed: {e}", exc_info=True)
        errors.append("drought")
        error_details["drought"] = str(e)

    try:
        moisture = compute_soil_moisture(aoi=aoi, start_date=start_date, end_date=end_date)
        moisture_metrics = moisture["metrics"]
    except Exception as e:
        logger.warning(f"risk_scoring: soil_moisture failed: {e}", exc_info=True)
        errors.append("moisture")
        error_details["moisture"] = str(e)

    sub_scores = {
        "drought": _drought_subscore(drought_metrics) if "drought" not in errors else None,
        "vegetation_loss": _vegetation_subscore(veg_metrics) if "vegetation" not in errors else None,
        "moisture_deficit": _moisture_subscore(moisture_metrics) if "moisture" not in errors else None,
    }

    available = {k: v for k, v in sub_scores.items() if v is not None}
    if not available:
        raise RuntimeError(f"All risk sub-scores failed to compute: {error_details}")

    # Renormalize weights over whatever actually computed successfully —
    # this is also why confidence drops when inputs are missing (below).
    used_weight = sum(WEIGHTS[k] for k in available)
    composite = sum(available[k] * WEIGHTS[k] for k in available) / used_weight
    composite = round(_clamp(composite), 1)

    band = (
        "severe" if composite >= 75 else
        "high" if composite >= 55 else
        "moderate" if composite >= 30 else
        "low"
    )

    confidence = _confidence(available, errors, region_id)

    reason = _explain(composite, band, sub_scores, drought_metrics, veg_metrics, moisture_metrics)

    inputs_failed = [k for k, v in sub_scores.items() if v is None]

    return {
        "risk_score": composite,
        "band": band,
        "confidence": confidence,
        "reason": reason,
        "sub_scores": sub_scores,
        "inputs_used": list(available.keys()),
        "inputs_failed": inputs_failed,
        "period": {"start_date": start_date, "end_date": end_date},
        "raw_metrics": {
            "vegetation": veg_metrics,
            "drought": drought_metrics,
            "soil_moisture": moisture_metrics,
        },
        "provenance": {
            "vegetation_source": "Sentinel-2 (COPERNICUS/S2_SR_HARMONIZED)",
            "drought_source": "Sentinel-2 NDDI",
            "moisture_source": "NASA_USDA/HSL/SMAP10KM_soil_moisture",
            "computed_at": datetime.utcnow().isoformat() + "Z",
        },
    }


def _confidence(available: Dict[str, float], errors: list, region_id: Optional[str]) -> float:
    """
    Confidence score per the ground-truth/trust requirement: never claim
    false precision. Starts from data completeness (fewer failed inputs =
    higher confidence), then folds in the region's actual track record from
    farmer/officer feedback if there's enough history to matter — this is
    the compounding trust loop, not a static number.
    """
    completeness = len(available) / 3.0
    base = 0.55 + 0.45 * completeness  # 0.55-1.0 depending on data completeness

    if region_id:
        track_record = db.feedback_accuracy_rate(region_id=region_id)
        if track_record["total_feedback"] >= 5:
            # blend in real accuracy once there's enough feedback to be meaningful
            base = round((base + track_record["accuracy_rate"]) / 2, 4)

    return round(_clamp(base, 0.0, 1.0) * 100, 1)  # expressed as 0-100 for consistency with score


def _explain(score: float, band: str, sub_scores: Dict, drought_m: Dict, veg_m: Dict, moist_m: Dict) -> str:
    """Plain-language reasoning for why the score fired — the explainability
    layer the ground-truth/trust requirement calls for, not just a number."""
    parts = []
    if sub_scores.get("drought") and sub_scores["drought"] > 40:
        parts.append("elevated drought stress")
    if sub_scores.get("vegetation_loss") and sub_scores["vegetation_loss"] > 20:
        loss_pct = veg_m.get("loss_pct")
        parts.append(f"vegetation loss of {loss_pct:.1f}% over the period" if loss_pct else "notable vegetation loss")
    if sub_scores.get("moisture_deficit") and sub_scores["moisture_deficit"] > 40:
        parts.append("declining soil moisture")

    missing = [k.replace("_", " ") for k, v in sub_scores.items() if v is None]
    missing_note = ""
    if missing:
        missing_note = (
            f" Note: {', '.join(missing)} could not be assessed (no satellite coverage for this AOI/"
            f"period) and is excluded from this score rather than assumed low-risk."
        )

    if not parts:
        base = f"Risk is {band} — no single indicator stands out; conditions look broadly stable for the period analyzed."
        return base + missing_note

    return f"Risk is {band} ({score}/100), driven mainly by: " + ", ".join(parts) + "." + missing_note
