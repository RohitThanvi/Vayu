"""
irrigation_advisory.py - turns soil moisture + rainfall context into one plain recommendation: irrigate now, hold off, or monitor.

v2 adds two things the surface-only read could not see:
  * ROOT-ZONE moisture (root_zone.py): SMAP L4 root zone (0-100 cm) percentile, modelled, plus ERA5-Land layers for reference. Surface soil dries within days of
    a rain event while the root zone stays buffered, so a dry 0-5 cm layer over an adequate root zone is NOT an irrigation signal on its own.
  * CROP STAGE (crop_stages.py, optional): given a crop and sowing date, FAO-56 stage and Kc say how much water the crop is using now. Peak demand is mid-season;
    crops that are allowed to dry down (Kc_end < 0.45) need little or no irrigation late in the cycle.

Still deliberately conservative: SMAP/ERA5 are ~10 km modelled reads and CHIRPS is ~5.5 km. This is a regional, directional signal, not a field-level prescription,
and it knows nothing about field capacity, soil depth or the irrigation system. The decision thresholds are heuristics (stated in the response), not ICAR norms.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from ..gee_client import compute_soil_moisture
from .crop_stages import stage_info
from .precipitation import compute_precipitation_context
from .root_zone import fetch_root_zone, summarize_root_zone

logger = logging.getLogger(__name__)

# Thresholds on dry_stress_pct (share of AOI with SMAP surface soil
# moisture < 0.1 m3/m3 — the same dry-stress mask risk_scoring.py's
# moisture subscore already uses, reused here rather than inventing a
# second dryness definition).
_DRY_STRESS_HIGH = 50.0   # most of the AOI reading dry
_DRY_STRESS_LOW = 20.0    # only a small share reading dry
# Rainfall anomaly thresholds — matches precipitation.py's own condition bands.
_RAIN_WELL_BELOW_NORMAL = -25.0


def compute_irrigation_advisory(aoi: Dict[str, Any], as_of: Optional[str] = None, crop_id: Optional[str] = None,
                                sowing_date: Optional[str] = None) -> Dict[str, Any]:
    end_date = as_of or datetime.utcnow().strftime("%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    start_date = (end_dt - timedelta(days=90)).strftime("%Y-%m-%d")

    logger.info(f"agri irrigation advisory: {start_date} -> {end_date}")
    try:
        moisture = compute_soil_moisture(aoi=aoi, start_date=start_date, end_date=end_date)["metrics"]
    except Exception as e:
        logger.warning(f"irrigation advisory: soil moisture failed: {e}")
        moisture = {"data_available": False}

    rainfall = compute_precipitation_context(aoi=aoi, as_of=end_date)
    try:
        root_zone = summarize_root_zone(fetch_root_zone(aoi, end_date))
    except Exception as e:
        logger.warning(f"irrigation advisory: root zone failed: {e}")
        root_zone = summarize_root_zone({"errors": [str(e)]})
    stage = stage_info(crop_id, sowing_date, end_date) if crop_id else None

    dry_pct = moisture.get("dry_stress_pct")
    moisture_ok = moisture.get("data_available", False) and dry_pct is not None
    rain_ok = rainfall.get("status") == "ok"
    rain_anomaly = rainfall.get("anomaly_pct")

    if not moisture_ok:
        return {
            "recommendation": "insufficient_data",
            "reason": "No usable SMAP soil-moisture reading for this AOI/period — can't ground an irrigation "
                      "read without it. Rainfall context (below) is still shown for reference.",
            "soil_moisture": moisture,
            "rainfall": rainfall,
            "method": "SMAP L4 soil moisture (dry-stress share of AOI) + CHIRPS rainfall vs seasonal-normal.",
        }

    recommendation, reason, urgency, basis = decide_irrigation(dry_pct, rain_ok, rain_anomaly, rainfall.get("condition"), root_zone, stage)

    return {
        "recommendation": recommendation,
        "reason": reason,
        "urgency": urgency,
        "decision_basis": basis,
        "root_zone": root_zone,
        "crop_stage": stage,
        "soil_moisture": {
            "dry_stress_pct": dry_pct,
            "moisture_change": moisture.get("moisture_change"),
            "end_avg_soil_moisture": moisture.get("end_avg_soil_moisture"),
        },
        "rainfall": {
            "status": rainfall.get("status"),
            "condition": rainfall.get("condition"),
            "anomaly_pct": rain_anomaly,
            "recent_total_mm": rainfall.get("recent_total_mm"),
        },
        "disclaimer": "Regional read (SMAP/ERA5-Land ~10km modelled, CHIRPS ~5.5km) with no knowledge of field capacity, soil depth or the irrigation system"
                       + ("; crop stage is FAO-56 indicative, not a local calendar" if stage and stage.get("status") == "ok" else "; no crop or growth stage was supplied")
                       + " — a directional signal, not a field-level agronomic prescription.",
        "method": "SMAP L4 soil moisture (dry-stress share of AOI, same definition risk_scoring.py's moisture "
                   "subscore uses) cross-checked against CHIRPS rainfall vs. seasonal-normal.",
    }


def _surface_only(dry_pct, rain_ok, rain_anomaly, rain_condition):
    """The original v1 logic, used when no root-zone reading exists. Unchanged behaviour."""
    if dry_pct >= _DRY_STRESS_HIGH and (not rain_ok or (rain_anomaly is not None and rain_anomaly <= _RAIN_WELL_BELOW_NORMAL)):
        return "irrigate_now", (f"{round(dry_pct)}% of the AOI is reading dry soil moisture (SMAP), and recent rainfall is "
                                f"{'well below normal' if rain_ok else 'not available to check'} — both signals point the same way.")
    if dry_pct >= _DRY_STRESS_HIGH:
        return "monitor", (f"{round(dry_pct)}% of the AOI is reading dry soil moisture (SMAP), but recent rainfall looks {rain_condition or 'uncertain'} — "
                           f"SMAP can lag a recent rain event by a few days, worth checking again before acting.")
    if dry_pct <= _DRY_STRESS_LOW:
        return "hold_off", f"Only {round(dry_pct)}% of the AOI is reading dry — soil moisture currently looks adequate."
    return "monitor", f"{round(dry_pct)}% of the AOI is reading dry — moderate, worth watching rather than acting on immediately."


def decide_irrigation(dry_pct, rain_ok, rain_anomaly, rain_condition, root_zone=None, stage=None):
    """Pure decision. Returns (recommendation, reason, urgency, basis). Root zone leads when present; the surface layer alone never forces 'irrigate_now'."""
    basis = []
    rz = (root_zone or {}).get("status", "unknown")
    pctl = (root_zone or {}).get("sm_rootzone_percentile")
    rain_low = (not rain_ok) or (rain_anomaly is not None and rain_anomaly <= _RAIN_WELL_BELOW_NORMAL)
    if rz == "unknown":
        rec, reason = _surface_only(dry_pct, rain_ok, rain_anomaly, rain_condition)
        urgency = "high" if rec == "irrigate_now" else "normal"
        basis.append("surface SMAP only (no root-zone reading available for this AOI/period)")
    else:
        basis.append(f"SMAP L4 root zone (0-100 cm, modelled) at the {pctl}th percentile: {rz.replace('_', ' ')}")
        if rz == "dry":
            rec, urgency = ("irrigate_now", "high") if rain_low else ("monitor", "normal")
            reason = (f"Root-zone moisture is dry (SMAP L4 percentile {pctl}), and recent rainfall is {'well below normal or unavailable' if rain_low else (rain_condition or 'not low')}"
                      + (" — both signals point the same way." if rain_low else " — recent rain may still be recharging it; check again before acting."))
        elif rz == "below_normal":
            rec, urgency = "monitor", "normal"
            reason = f"Root-zone moisture is below normal (percentile {pctl}) but not dry — worth watching."
        else:
            rec, urgency = "hold_off", "normal"
            reason = f"Root-zone moisture looks adequate (percentile {pctl})"
            if dry_pct is not None and dry_pct >= _DRY_STRESS_HIGH:
                reason += f"; the {round(dry_pct)}% of the AOI reading dry is the 0-5 cm surface layer, which dries quickly after rain while the root zone stays buffered."
            else:
                reason += "."
    if stage and stage.get("status") == "ok":
        basis.append(f"FAO-56 stage: {stage['stage_label']}, day {stage['stage_day']}/{stage['stage_length']}, Kc {stage['kc']} ({int(stage['relative_demand'] * 100)}% of peak demand)")
        if stage["stage"] == "late season" and stage["dry_down_crop"] and rec == "irrigate_now":
            rec, urgency = "monitor", "low"
            reason += f" However {stage['crop_name']} is in its late season; FAO-56 treats crops like this as allowed to dry down, with little or no irrigation needed now."
        elif stage["stage"] == "mid-season" and rec == "monitor" and rz in ("dry", "below_normal") and rain_low:
            rec, urgency = "irrigate_now", "high"
            reason += f" {stage['crop_name']} is in mid-season, when its water use peaks, so this is treated as urgent."
        elif stage["stage"] == "mid-season" and rec == "irrigate_now":
            urgency = "high"; reason += f" {stage['crop_name']} is in mid-season (peak water use)."
    elif stage:
        basis.append(f"crop stage not applied: {stage.get('note', stage.get('status'))}")
    return rec, reason, urgency, basis
