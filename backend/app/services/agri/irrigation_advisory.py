"""
irrigation_advisory.py — turns soil moisture + rainfall data that already
exist as separate raw context (gee_client.compute_soil_moisture,
precipitation.py) into one plain recommendation: irrigate now, hold off,
or monitor. Same "condition snapshot -> actionable read" move risk_scoring.py
makes for the overall risk score, applied specifically to the irrigation
decision.

Deliberately conservative about what it claims: SMAP soil moisture is
~10km resolution and CHIRPS rainfall is ~5.5km — this is a regional read,
not a field-level agronomic recommendation, and it has no knowledge of
crop type, growth stage, field capacity, or irrigation system. Stated
plainly in every response rather than presented as more precise than it
is (same principle as groundwater.py's resolution_note).
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from ..gee_client import compute_soil_moisture
from .precipitation import compute_precipitation_context

logger = logging.getLogger(__name__)

# Thresholds on dry_stress_pct (share of AOI with SMAP surface soil
# moisture < 0.1 m3/m3 — the same dry-stress mask risk_scoring.py's
# moisture subscore already uses, reused here rather than inventing a
# second dryness definition).
_DRY_STRESS_HIGH = 50.0   # most of the AOI reading dry
_DRY_STRESS_LOW = 20.0    # only a small share reading dry
# Rainfall anomaly thresholds — matches precipitation.py's own condition bands.
_RAIN_WELL_BELOW_NORMAL = -25.0


def compute_irrigation_advisory(aoi: Dict[str, Any], as_of: Optional[str] = None) -> Dict[str, Any]:
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

    # Decision logic: dryness alone doesn't say whether it's WORTH irrigating
    # right now vs. rain is already handling it — cross-checking against the
    # rainfall anomaly is what turns "the ground is dry" into an actual
    # recommendation, same reasoning a human advisory would use.
    if dry_pct >= _DRY_STRESS_HIGH and (not rain_ok or (rain_anomaly is not None and rain_anomaly <= _RAIN_WELL_BELOW_NORMAL)):
        recommendation = "irrigate_now"
        reason = (
            f"{round(dry_pct)}% of the AOI is reading dry soil moisture (SMAP), and recent rainfall is "
            f"{'well below normal' if rain_ok else 'not available to check'} — both signals point the same way."
        )
    elif dry_pct >= _DRY_STRESS_HIGH:
        recommendation = "monitor"
        reason = (
            f"{round(dry_pct)}% of the AOI is reading dry soil moisture (SMAP), but recent rainfall looks "
            f"{rainfall.get('condition', 'uncertain')} — SMAP can lag a recent rain event by a few days, "
            f"worth checking again before acting."
        )
    elif dry_pct <= _DRY_STRESS_LOW:
        recommendation = "hold_off"
        reason = f"Only {round(dry_pct)}% of the AOI is reading dry — soil moisture currently looks adequate."
    else:
        recommendation = "monitor"
        reason = f"{round(dry_pct)}% of the AOI is reading dry — moderate, worth watching rather than acting on immediately."

    return {
        "recommendation": recommendation,
        "reason": reason,
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
        "disclaimer": "Regional read (SMAP ~10km, CHIRPS ~5.5km) with no knowledge of crop type, growth "
                       "stage, or field capacity — a directional signal, not a field-level agronomic prescription.",
        "method": "SMAP L4 soil moisture (dry-stress share of AOI, same definition risk_scoring.py's moisture "
                   "subscore uses) cross-checked against CHIRPS rainfall vs. seasonal-normal.",
    }
