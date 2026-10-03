"""
water_balance.py - monthly soil-water balance for dry-window crops (rabi, zaid). Pure Python, no GEE.

Why: annual rainfall is a poor stand-in for the water a Nov-Mar or Mar-Jun crop actually has. A site with 3000 mm/yr that falls in
Jun-Sep is dry in winter, and what a rainfed winter crop lives on is the moisture the soil STORED after the monsoon, which depends on
soil texture. This module estimates:   supply = in-window rain + stored soil water at the window start,
                                        demand = Kc * PET over the window,    moisture index (MAI) = supply / demand.

Method (all simple, standard, documented; every number marked ESTIMATE is an assumption):
  * PET: Thornthwaite (1948) from monthly mean temperature, with the Willmott et al. (1985) extension above 26.5 C and a day-length
    correction from latitude. It under-estimates in hot-dry climates, so MAI is a RELATIVE index, not an irrigation-scheduling figure.
  * Storage capacity = plant-available water capacity (AWC, by USDA texture) x root-zone depth.
  * Bucket: S <- min(cap, S + P - min(S + P, FALLOW_ET_FRACTION * PET)) month by month, two passes of the climatology so the second pass
    starts from a realistic state. Surplus above capacity is lost to runoff / drainage.
  * Demand uses one cycle-mean crop coefficient (FAO-56 stage shape).
Limits: soil DEPTH is unknown (OpenLandMap gives 0-30 cm); shallow soils (e.g. laterite) hold less than ROOT_ZONE_M x AWC, so rainfed estimates
can be optimistic. This is surfaced as a caveat and a confidence penalty.
"""
import math
from typing import Dict, List, Optional

# Plant-available water capacity, mm of water per metre of soil, by USDA texture class code (see crop_requirements.TEXTURE_NAMES).
# Source: UC Cooperative Extension "Understanding Soil Water Holding Characteristics" (ANR 80243), inches/ft x 83.3 at the range midpoint:
#   sandy loams 1.25-1.75 -> 125; loams / silt loams 1.5-2.3 -> 158; clay loams / silty clay loams / sandy clay loams 1.75-2.5 -> 177;
#   sandy clays / silty clays / clays 1.6-2.5 -> 171. Loamy sand 112 mm/m: Alberta Agriculture. Sand 90: ESTIMATE (below loamy sand).
AWC_MM_PER_M: Dict[int, float] = {1: 171, 2: 171, 3: 171, 4: 177, 5: 177, 6: 177, 7: 158, 8: 158, 9: 125, 10: 158, 11: 112, 12: 90}
ROOT_ZONE_M = 1.0                 # ESTIMATE: effective root-zone depth; FAO-56 maximum root depths are 0.5-1.5 m
KC_CYCLE_MEAN = 0.75              # FAO-56 Table 12 wheat shape (Kc 0.3 -> 1.15 -> 0.25 over 30/30/40/30 days) averages ~0.75; ESTIMATE for other crops
FALLOW_ET_FRACTION = 0.5          # ESTIMATE: share of PET lost from a fallow / weedy field between crops
MAI_ZERO = 0.30                   # ESTIMATE: moisture index at/below which rainfed score is 0
MAI_FULL = 0.80                   # ESTIMATE: moisture index at/above which rainfed supply is adequate
_DAYS = [31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
_MID_DOY = [15, 46, 74, 105, 135, 166, 196, 227, 258, 288, 319, 349]


def _daylength_hours(lat_deg: Optional[float], doy: int) -> float:
    if lat_deg is None:
        return 12.0
    phi = math.radians(max(-66.0, min(66.0, lat_deg)))
    delta = 0.409 * math.sin(2 * math.pi * doy / 365.0 - 1.39)
    x = -math.tan(phi) * math.tan(delta)
    return 24.0 / math.pi * math.acos(max(-1.0, min(1.0, x)))


def thornthwaite_pet(monthly_temp_c: List[Optional[float]], lat_deg: Optional[float] = None) -> Optional[List[float]]:
    """Monthly PET in mm. None if any temperature is missing."""
    if not monthly_temp_c or any(t is None for t in monthly_temp_c):
        return None
    heat = sum((t / 5.0) ** 1.514 for t in monthly_temp_c if t > 0)
    if heat <= 0:
        return [0.0] * 12
    a = 6.75e-7 * heat ** 3 - 7.71e-5 * heat ** 2 + 1.792e-2 * heat + 0.49239
    out = []
    for i, t in enumerate(monthly_temp_c):
        if t <= 0:
            pet = 0.0
        elif t >= 26.5:
            pet = -415.85 + 32.24 * t - 0.43 * t * t
        else:
            pet = 16.0 * (10.0 * t / heat) ** a
        out.append(max(0.0, pet * (_daylength_hours(lat_deg, _MID_DOY[i]) / 12.0) * (_DAYS[i] / 30.0)))
    return out


def awc_mm(texture_class: Optional[int], depth_m: float = ROOT_ZONE_M) -> Optional[float]:
    return None if texture_class not in AWC_MM_PER_M else AWC_MM_PER_M[texture_class] * depth_m


def stored_water_by_month(monthly_rain: List[Optional[float]], pet: List[float], capacity: float) -> Optional[List[float]]:
    """Soil water (mm) at the END of each calendar month for the climatological year."""
    if not monthly_rain or any(r is None for r in monthly_rain) or pet is None or capacity is None:
        return None
    s, series = 0.0, []
    for _ in range(2):                                   # spin-up pass, then the pass we keep
        series = []
        for m in range(12):
            avail = s + monthly_rain[m]
            s = min(capacity, avail - min(avail, FALLOW_ET_FRACTION * pet[m]))
            series.append(s)
    return series


def build_water_balance(monthly_rain, monthly_temp, texture_class, lat_deg=None) -> Optional[Dict]:
    pet = thornthwaite_pet(monthly_temp, lat_deg)
    cap = awc_mm(texture_class)
    stored = stored_water_by_month(monthly_rain, pet, cap) if pet is not None and cap is not None else None
    if stored is None:
        return None
    return {"pet_mm": [round(v, 1) for v in pet], "stored_mm_end_of_month": [round(v, 1) for v in stored], "awc_mm": round(cap, 1),
            "root_zone_m": ROOT_ZONE_M, "kc_cycle_mean": KC_CYCLE_MEAN, "lat_used": lat_deg,
            "method": "Thornthwaite PET + monthly bucket; AWC by texture (UC ANR 80243); see water_balance.py"}


def window_balance(wb: Dict, monthly_rain, months: List[int]) -> Dict:
    """Supply / demand over a season window. months are 1-12 in order, possibly wrapping the year end."""
    first = months[0]
    prev = (first - 2) % 12                              # index of the month BEFORE the window starts
    stored = wb["stored_mm_end_of_month"][prev]
    rain = sum(monthly_rain[m - 1] for m in months)
    pet = sum(wb["pet_mm"][m - 1] for m in months)
    demand = wb["kc_cycle_mean"] * pet
    supply = rain + stored
    mai = None if demand <= 0 else supply / demand
    score = 1.0 if mai is None else max(0.0, min(1.0, (mai - MAI_ZERO) / (MAI_FULL - MAI_ZERO)))
    return {"window_rain_mm": rain, "stored_mm": stored, "supply_mm": supply, "pet_window_mm": pet, "demand_mm": demand,
            "mai": mai, "rainfed_score": score}
