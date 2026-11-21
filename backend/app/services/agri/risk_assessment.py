"""
risk_assessment.py - the DETAILED agricultural risk assessment (v2). It sits next to the original 3-indicator score (risk_scoring.py, kept unchanged for alerts and the watchlist)
and fixes its main weaknesses: ad hoc scalings, no baseline, three inputs, and no land context.

Framework (IPCC AR5 WGII: risk = hazard x exposure x vulnerability, reported as parts, not one opaque number):
  STRESS (hazard, time-varying). Each indicator is compared with ITS OWN multi-year history and mapped to a standard class:
      vegetation   VHI = 0.5 VCI + 0.5 TCI (Kogan; Spectra drought index) and NDVI z-score vs the seasonal normal
      rainfall     recent 90-day total as a percentile of 30 same-window years (CHIRPS), plus an SPI-like z
      moisture     SMAP L4 root-zone soil-moisture percentile (modelled)
      groundwater  latest GRACE anomaly as a percentile of its own record (short, low reliability)
    Percentile indicators use the US Drought Monitor bands (0-2nd, 3-5th, 6-10th, 11-20th, 21-30th); VHI uses Kogan's classes. Ordinal classes are scaled to 0-100 stress
    points (20 per USDM category, 25 per VHI class): a convention, not a measurement. Stress = mean of the available family means (equal family weights).
  VULNERABILITY (static land context): soil water-holding (AWC by texture), organic carbon (Soil Health Card classes), slope (AIS&LUS classes), aridity (UNEP classes), groundwater
    trend. Rated on documented ordinal scales and averaged. pH, salinity and nutrients are fertility constraints, not drought vulnerability: they are described in the SOIL CONDITION
    panel and never enter a score.
  RISK CLASS = the stress class shifted one step up when vulnerability is high and one down when low (never outside Low..Very high). A rule, not a fitted model.
Honesty: nothing here is validated against yield or loss data yet. Weights are equal by design and their influence is SHOWN (leave-one-family-out and alternative weightings give a
score range); confidence = family coverage x indicator reliability. Every indicator fails independently and is reported as unavailable with the reason, never as zero.
"""
import logging
import math
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from . import engine_config as cfg
from .crop_requirements import TEXTURE_NAMES
from .water_balance import AWC_MM_PER_M, thornthwaite_pet

logger = logging.getLogger(__name__)
NORMAL = statistics.NormalDist()
FAMILIES = ("vegetation", "rainfall", "soil_moisture", "groundwater")
FAMILY_LABEL = {"vegetation": "Vegetation health", "rainfall": "Rainfall", "soil_moisture": "Soil moisture", "groundwater": "Groundwater"}
RELIABILITY_POINTS = {"high": 1.0, "medium": 0.7, "low": 0.4}
STRESS_CLASSES = [(25, "Low"), (50, "Moderate"), (75, "High"), (101, "Very high")]
RISK_ORDER = ["Low", "Moderate", "High", "Very high"]
SENSITIVITY_ROBUST_RANGE = 10.0          # score range (points) under alternative weightings at or below which the score is called robust

CITATIONS = {
    "ipcc": "IPCC (2014) Climate Change 2014: Impacts, Adaptation and Vulnerability, AR5 WGII: risk as the interaction of hazard, exposure and vulnerability.",
    "kogan": "Kogan, F.N. (1995) Application of vegetation index and brightness temperature for drought detection. Advances in Space Research 15(11):91-100 (VCI, TCI, VHI). Class limits as summarised in UN-SPIDER's drought-detection recommended practice.",
    "usdm": "US Drought Monitor percentile-rank classification (D0 21st-30th, D1 11th-20th, D2 6th-10th, D3 3rd-5th, D4 0-2nd percentile): droughtmonitor.unl.edu/About/AbouttheData/DroughtClassification.aspx",
    "spi": "McKee, T.B., Doesken, N.J., Kleist, J. (1993) The relationship of drought frequency and duration to time scales. 8th Conference on Applied Climatology, AMS (SPI).",
    "chirps": "Funk, C. et al. (2015) The climate hazards infrared precipitation with stations (CHIRPS). Scientific Data 2:150066.",
    "smap": "NASA SMAP Level-4 Global 3-hourly 9 km Surface and Root Zone Soil Moisture (SPL4SMGP): modelled, SMAP brightness temperature assimilated into a land-surface model.",
    "grace": "NASA GRACE / GRACE-FO mascon liquid-water-equivalent thickness: a regional (~300 km) storage signal, not a well-level measurement.",
    "shc": "Soil Health Card rating limits (Government of India Soil Health Card scheme; ICAR-IISS): organic carbon low <0.50, medium 0.50-0.75, high >0.75 percent; soil reaction acidic <6.5, neutral 6.5-7.0, alkaline >7.0.",
    "aislus": "Slope classes of the All India Soil and Land Use Survey as used by ICAR-NBSS&LUP: 0-1, 1-3, 3-5, 5-10, 10-15, 15-25, 25-33, 33-50, >50 percent.",
    "unep": "UNEP (1992) World Atlas of Desertification: aridity index P/PET, hyper-arid <0.05, arid 0.05-0.20, semi-arid 0.20-0.50, dry sub-humid 0.50-0.65, humid >=0.65.",
    "awc": "Plant-available water capacity by USDA texture: UC Cooperative Extension ANR 80243 and Alberta Agriculture (see water_balance.py); sand is an estimate.",
    "openlandmap": "OpenLandMap / SoilGrids-style modelled soil layers (pH, organic carbon, texture) at 250 m: model estimates, not field measurements.",
}
LIMITATIONS = [
    "Not validated against yield or crop-loss data. The score is a documented, standardised stress read, not a forecast of loss.",
    "Ordinal classes are scaled to points by convention (20 per drought category); the points are not a measurement.",
    "Soil properties are 250 m model estimates; pH, organic carbon and texture can differ from the field. Nutrients, salinity, micronutrients and soil biology cannot be sensed from space: get a soil test (Soil Health Card).",
    "SMAP root-zone moisture is ~10 km model output; GRACE is ~300 km and its record here is short (low reliability).",
    "VHI, VCI and TCI are relative to the pixel's own record; a short record understates extremes.",
    "Crop type, sowing date, irrigation and management are unknown, so this is a land and season assessment, not a crop-specific one.",
]


# ----------------------------------------------------------------------------------------------------------- pure statistics and classes
def percentile_rank(value: Optional[float], sample: List[float]) -> Optional[float]:
    """Mid-rank percentile (0-100) of `value` within `sample` (ties count half). None for an empty sample."""
    vals = [float(v) for v in sample if v is not None and math.isfinite(v)]
    if value is None or not vals:
        return None
    below, equal = sum(v < value for v in vals), sum(v == value for v in vals)
    return round(100.0 * (below + 0.5 * equal) / len(vals), 2)


def z_to_percentile(z: Optional[float]) -> Optional[float]:
    return None if z is None else round(100.0 * NORMAL.cdf(z), 2)


def percentile_to_z(p: Optional[float]) -> Optional[float]:
    """SPI-like z from a percentile (clamped to 0.1-99.9 so a rank at the ends does not explode)."""
    return None if p is None else round(NORMAL.inv_cdf(min(max(p, 0.1), 99.9) / 100.0), 2)


def usdm_class(p: Optional[float]) -> Tuple[int, str]:
    """US Drought Monitor percentile bands -> (code 0-5, label). Lower percentile = drier / more stressed."""
    if p is None: return 0, "unknown"
    for upper, code, label in ((2, 5, "D4 exceptional"), (5, 4, "D3 extreme"), (10, 3, "D2 severe"), (20, 2, "D1 moderate"), (30, 1, "D0 abnormally dry")):
        if p <= upper: return code, label
    return 0, "no drought signal"


def usdm_stress(p: Optional[float]) -> Optional[float]:
    return None if p is None else usdm_class(p)[0] * 20.0


def vhi_class(vhi: Optional[float]) -> Tuple[int, str]:
    """Kogan VHI classes: >40 none, 30-40 mild, 20-30 moderate, 10-20 severe, <10 extreme -> (code 0-4, label)."""
    if vhi is None: return 0, "unknown"
    for limit, code, label in ((10, 4, "extreme"), (20, 3, "severe"), (30, 2, "moderate"), (40, 1, "mild")):
        if vhi < limit: return code, label
    return 0, "no drought signal"


def vhi_stress(vhi: Optional[float]) -> Optional[float]:
    return None if vhi is None else vhi_class(vhi)[0] * 25.0


def stress_level(score: Optional[float]) -> str:
    if score is None: return "unknown"
    return next(label for limit, label in STRESS_CLASSES if score < limit)


# ----------------------------------------------------------------------------------------------------------- indicators
def _ind(iid, name, family, value, unit, baseline, pct, cls, stress, rel, source, res, window, note=""):
    return {"id": iid, "name": name, "family": family, "status": "ok", "value": value, "unit": unit, "baseline": baseline, "percentile": pct, "class": cls, "stress": stress,
            "reliability": rel, "source": source, "resolution": res, "window": window, "note": note}


def _missing(iid, name, family, reason, source):
    return {"id": iid, "name": name, "family": family, "status": "unavailable", "reason": reason, "source": source, "stress": None, "reliability": None}


def _rel(n, hi, mid): return "high" if n >= hi else ("medium" if n >= mid else "low")


def ind_vhi(vdi: Optional[Dict[str, Any]], err: Optional[str] = None) -> Dict[str, Any]:
    nm, src = "Vegetation Health Index (VHI)", "MODIS NDVI 250 m + LST 1 km (Spectra drought index)"
    if not vdi or "indices" not in vdi: return _missing("vhi", nm, "vegetation", err or "drought index not available for this area/period", src)
    vhi = (vdi["indices"].get("VHI") or {}); mean = vhi.get("mean"); valid = vdi.get("valid_pixel_fraction")
    if mean is None or (valid is not None and valid < 0.2): return _missing("vhi", nm, "vegetation", f"too few valid pixels ({valid})", src)
    base = vdi.get("baseline") or {}; yrs = min(base.get("ndvi_years_with_data") or 0, base.get("lst_years_with_data") or 0)
    code, label = vhi_class(mean); comp = {k: (vdi["indices"].get(k) or {}).get("mean") for k in ("VCI", "TCI")}
    return _ind("vhi", nm, "vegetation", round(mean, 1), "0-100", f"per-pixel record {base.get('years_requested', 'n/a')}; {yrs} years with data", None, f"{label} (Kogan)", vhi_stress(mean),
                _rel(yrs, 15, 8), src, "1 km", base.get("window", ""), f"VCI {comp['VCI']} (vegetation condition), TCI {comp['TCI']} (temperature condition). Valid pixels {valid}.")


def ind_ndvi(base: Optional[Dict[str, Any]], err: Optional[str] = None) -> Dict[str, Any]:
    nm, src = "NDVI anomaly vs seasonal normal", "Sentinel-2 SR (10 m)"
    if not base or base.get("z_score") is None: return _missing("ndvi_z", nm, "vegetation", err or (base or {}).get("reason") or "no seasonal baseline", src)
    p = z_to_percentile(base["z_score"]); code, label = usdm_class(p)
    return _ind("ndvi_z", nm, "vegetation", base["z_score"], "z-score", f"normal {base.get('seasonal_normal_ndvi')} +/- {base.get('seasonal_std_ndvi')} over {base.get('years_used')} years", p, label, usdm_stress(p),
                _rel(base.get("years_used") or 0, 10, 6), src, "10 m", f"{base.get('window_days')}-day window to {base.get('as_of')}", f"current NDVI {base.get('current_ndvi')}; status {base.get('status')}")


def ind_rain(prec: Optional[Dict[str, Any]], err: Optional[str] = None) -> Dict[str, Any]:
    nm, src = "Rainfall percentile (SPI-like)", "CHIRPS daily (5.5 km)"
    if not prec or prec.get("status") != "ok" or not prec.get("historical_totals_mm"): return _missing("rain_pct", nm, "rainfall", err or (prec or {}).get("status") or "rainfall history unavailable", src)
    hist = prec["historical_totals_mm"]; p = percentile_rank(prec["recent_total_mm"], hist); z = percentile_to_z(p); code, label = usdm_class(p)
    return _ind("rain_pct", nm, "rainfall", prec["recent_total_mm"], "mm", f"mean {prec['historical_mean_mm']} mm over {len(hist)} same-window years", p, label, usdm_stress(p), _rel(len(hist), 30, 15), src, "5.5 km",
                f"last {prec['recent_days']} days", f"SPI-like z = {z} (nonparametric, empirical percentile; WMO SPI recommends at least 30 years)")


def ind_rootzone(rz: Optional[Dict[str, Any]], err: Optional[str] = None) -> Dict[str, Any]:
    nm, src = "Root-zone soil moisture percentile", "SMAP L4 (modelled, ~10 km)"
    pct = (rz or {}).get("sm_rootzone_percentile")
    if not rz or pct is None: return _missing("rz_pct", nm, "soil_moisture", err or "SMAP root-zone data not available", src)
    code, label = usdm_class(pct)
    return _ind("rz_pct", nm, "soil_moisture", pct, "percentile", "percentile as defined by the SMAP L4 product", pct, label, usdm_stress(pct), "medium", src, "~10 km", "last 14 days",
                f"root zone {rz.get('sm_rootzone_0_100cm')} m3/m3 (0-100 cm), surface {rz.get('sm_surface_0_5cm')} m3/m3 (0-5 cm); modelled values, not measurements")


def ind_groundwater(gw: Optional[Dict[str, Any]], err: Optional[str] = None) -> Dict[str, Any]:
    nm, src = "Groundwater-storage percentile (GRACE)", "NASA GRACE/GRACE-FO mascons (~300 km)"
    series = [x["value"] for x in (gw or {}).get("series") or []]
    if not gw or len(series) < 12 or gw.get("latest_anomaly_cm") is None: return _missing("gw_pct", nm, "groundwater", err or (gw or {}).get("status") or "GRACE record too short or unavailable", src)
    p = percentile_rank(gw["latest_anomaly_cm"], series); code, label = usdm_class(p)
    return _ind("gw_pct", nm, "groundwater", gw["latest_anomaly_cm"], "cm water equivalent", f"percentile within its own {len(series)}-month record", p, label, usdm_stress(p), "low", src, "~300 km",
                f"to {gw.get('latest_date')}", f"trend {gw.get('trend')} ({gw.get('slope_cm_per_year')} cm/yr). Regional signal with a short record: low reliability.")


# ----------------------------------------------------------------------------------------------------------- combination, sensitivity, classes
def family_scores(indicators: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {}
    for f in FAMILIES:
        vals = [i["stress"] for i in indicators if i["family"] == f and i["status"] == "ok" and i["stress"] is not None]
        out[f] = round(sum(vals) / len(vals), 1) if vals else None
    return out


def combine(fam: Dict[str, Optional[float]], weights: Optional[Dict[str, float]] = None) -> Optional[float]:
    """Weighted mean over the AVAILABLE families (weights renormalised); None if none are available."""
    w = {f: (weights or {}).get(f, 1.0) for f in FAMILIES if fam.get(f) is not None}
    tot = sum(w.values())
    return None if not w or tot <= 0 else round(sum(fam[f] * w[f] for f in w) / tot, 1)


ALT_WEIGHTINGS = {"vegetation-heavy": {"vegetation": 4, "rainfall": 2, "soil_moisture": 2, "groundwater": 2}, "rainfall-heavy": {"vegetation": 2, "rainfall": 4, "soil_moisture": 2, "groundwater": 2},
                  "moisture-heavy": {"vegetation": 2, "rainfall": 2, "soil_moisture": 4, "groundwater": 2}, "groundwater-down-weighted": {"vegetation": 3, "rainfall": 3, "soil_moisture": 3, "groundwater": 1}}


def sensitivity(fam: Dict[str, Optional[float]]) -> Dict[str, Any]:
    base = combine(fam)
    variants: Dict[str, Optional[float]] = {"equal weights (headline)": base}
    for name, w in ALT_WEIGHTINGS.items(): variants[name] = combine(fam, w)
    avail = [f for f in FAMILIES if fam.get(f) is not None]
    if len(avail) > 1:
        for f in avail: variants[f"without {FAMILY_LABEL[f].lower()}"] = combine({k: (None if k == f else v) for k, v in fam.items()})
    vals = [v for v in variants.values() if v is not None]
    rng = round(max(vals) - min(vals), 1) if vals else None
    swing = max(((n, abs(v - base)) for n, v in variants.items() if v is not None and base is not None), key=lambda x: x[1], default=(None, 0.0))
    return {"variants": variants, "min": min(vals) if vals else None, "max": max(vals) if vals else None, "range": rng, "robust": (rng is not None and rng <= SENSITIVITY_ROBUST_RANGE),
            "most_influential": swing[0] if swing[1] > 0 else None}


def confidence(indicators: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok = [i for i in indicators if i["status"] == "ok"]
    fams = {i["family"] for i in ok}
    coverage = len(fams) / len(FAMILIES)
    rel = (sum(RELIABILITY_POINTS[i["reliability"]] for i in ok) / len(ok)) if ok else 0.0
    score = round(coverage * rel, 2)
    return {"score": score, "label": "high" if score >= 0.7 else ("medium" if score >= 0.45 else "low"), "coverage": f"{len(fams)} of {len(FAMILIES)} indicator families", "mean_reliability": round(rel, 2),
            "missing": [f for f in FAMILIES if f not in fams]}


def vuln_class(v: Optional[float]) -> str:
    return "unknown" if v is None else ("Low" if v < 100 / 3 else ("Moderate" if v < 200 / 3 else "High"))


def risk_class(stress: Optional[float], vuln: Optional[float]) -> Dict[str, Any]:
    lvl = stress_level(stress); vc = vuln_class(vuln)
    if lvl == "unknown": return {"class": "Cannot assess", "stress_class": lvl, "vulnerability_class": vc, "shift": 0, "rule": "No stress indicator was available."}
    shift = 1 if vc == "High" else (-1 if vc == "Low" else 0)
    i = min(max(RISK_ORDER.index(lvl) + shift, 0), len(RISK_ORDER) - 1)
    return {"class": RISK_ORDER[i], "stress_class": lvl, "vulnerability_class": vc, "shift": shift,
            "rule": f"Stress class {lvl}" + (f", moved {'up' if shift > 0 else 'down'} one step because land vulnerability is {vc.lower()}." if shift else f", unchanged (vulnerability {vc.lower()}).")}


# ----------------------------------------------------------------------------------------------------------- land context: vulnerability and soil condition
SLOPE_CLASSES = [(1, "nearly level", 0), (3, "very gently sloping", 10), (5, "gently sloping", 25), (10, "moderately sloping", 45), (15, "strongly sloping", 65), (25, "moderately steep", 80),
                 (33, "steep", 90), (50, "very steep", 100), (float("inf"), "extremely steep", 100)]
ARIDITY_CLASSES = [(0.05, "hyper-arid", 100), (0.20, "arid", 85), (0.50, "semi-arid", 60), (0.65, "dry sub-humid", 30), (float("inf"), "humid", 0)]
OC_CLASSES = [(0.50, "low", 80), (0.75, "medium", 40), (float("inf"), "high", 10)]            # Soil Health Card limits, percent
GW_TREND_POINTS = {"declining": 80.0, "decreasing": 80.0, "stable": 25.0, "rising": 0.0, "increasing": 0.0}
AWC_MAX, AWC_MIN = max(AWC_MM_PER_M.values()), min(AWC_MM_PER_M.values())


def _band(value, table):
    for limit, label, pts in table:
        if value < limit or limit == float("inf"): return label, pts
    return table[-1][1], table[-1][2]


def aridity_index(profile: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """UNEP aridity index = annual rainfall / annual PET (Thornthwaite from the monthly temperatures). Thornthwaite under-estimates PET in hot dry air, so this can flatter arid sites."""
    temps, rain = profile.get("monthly_temp_c"), profile.get("annual_rain_mm")
    if not temps or len(temps) != 12 or any(t is None for t in temps) or rain is None: return None
    try:
        pet = sum(thornthwaite_pet(temps, profile.get("lat") if profile.get("lat") is not None else 20.0))
    except Exception:
        return None
    if not pet or pet <= 0: return None
    ai = round(rain / pet, 2); label, pts = _band(ai, ARIDITY_CLASSES)
    return {"value": ai, "class": label, "points": pts, "annual_pet_mm": round(pet, 0)}


def vulnerability(profile: Dict[str, Any], gw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    f: List[Dict[str, Any]] = []
    tc = profile.get("texture_class")
    if tc in AWC_MM_PER_M:
        awc = AWC_MM_PER_M[tc]; pts = round(max(0.0, min(100.0, 100.0 * (AWC_MAX - awc) / (AWC_MAX - AWC_MIN))))
        f.append({"id": "awc", "name": "Soil water-holding capacity", "value": f"{awc} mm per m ({profile.get('texture_name') or TEXTURE_NAMES.get(tc, 'texture ' + str(tc))})", "class": "low capacity" if pts >= 66 else ("moderate" if pts >= 33 else "high capacity"),
                  "points": pts, "source": "awc, openlandmap", "note": "Less water held between rains means drought bites sooner. Rated against the span of USDA texture classes."})
    oc = profile.get("organic_carbon_gkg")
    if oc is not None:
        pct = round(oc / 10.0, 2); label, pts = _band(pct, OC_CLASSES)
        f.append({"id": "oc", "name": "Soil organic carbon", "value": f"{pct} %", "class": label, "points": pts, "source": "shc, openlandmap", "note": "Organic matter holds water and nutrients. Soil Health Card limits: low <0.50, medium 0.50-0.75, high >0.75 percent."})
    sl = profile.get("slope_pct")
    if sl is not None:
        label, pts = _band(sl, SLOPE_CLASSES)
        f.append({"id": "slope", "name": "Slope", "value": f"{round(sl, 1)} %", "class": label, "points": pts, "source": "aislus", "note": "Steeper land sheds rain as runoff and erodes; AIS&LUS classes."})
    ai = aridity_index(profile)
    if ai:
        f.append({"id": "aridity", "name": "Climatic aridity (P/PET)", "value": f"{ai['value']} (PET about {int(ai['annual_pet_mm'])} mm)", "class": ai["class"], "points": ai["points"], "source": "unep",
                  "note": "Annual rainfall over annual potential evapotranspiration (Thornthwaite); UNEP classes."})
    tr = str((gw or {}).get("trend") or "").lower()
    if gw and gw.get("status") == "ok" and tr in GW_TREND_POINTS:
        f.append({"id": "gw_trend", "name": "Groundwater-storage trend", "value": f"{tr} ({gw.get('slope_cm_per_year')} cm/yr)", "class": tr, "points": GW_TREND_POINTS[tr], "source": "grace",
                  "note": "A regional (~300 km) signal over a short record; low reliability."})
    pts = [x["points"] for x in f]
    score = round(sum(pts) / len(pts), 1) if pts else None
    return {"score": score, "class": vuln_class(score), "factors": f, "factors_used": len(f), "of": 5}


def soil_condition(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Descriptive panel in Soil Health Card terms. NOT scored: pH and salinity are fertility constraints, not drought vulnerability."""
    ph, oc, tc, sl = profile.get("ph"), profile.get("organic_carbon_gkg"), profile.get("texture_class"), profile.get("slope_pct")
    rows: List[Dict[str, Any]] = []
    if ph is not None:
        cls = "acidic" if ph < 6.5 else ("neutral" if ph <= 7.0 else "alkaline")
        extra = " Below about 5.5 (strongly acidic) phosphorus is often locked up and aluminium or manganese can be toxic (general knowledge)." if ph < 5.5 else (
            " Above about 8.5 sodicity (high exchangeable sodium) is possible and should be tested for (general knowledge)." if ph > 8.5 else "")
        rows.append({"id": "ph", "name": "Soil reaction (pH)", "value": round(ph, 1), "class": cls, "source": "shc, openlandmap", "interpretation": f"Soil Health Card reading: {cls}." + extra, "scored": False})
    if oc is not None:
        pct = round(oc / 10.0, 2); label, _ = _band(pct, OC_CLASSES)
        rows.append({"id": "oc", "name": "Organic carbon", "value": f"{pct} %", "class": label, "source": "shc, openlandmap", "interpretation": f"Soil Health Card rating: {label}. Build it with compost, crop residue and green manure.", "scored": True})
    if tc is not None:
        drain = "poorly drained (heavy)" if tc in cfg.POOR_DRAINING_TEXTURES else ("freely drained (light)" if tc in cfg.FREE_DRAINING_TEXTURES else "moderately drained")
        rows.append({"id": "texture", "name": "Texture and drainage", "value": profile.get("texture_name") or TEXTURE_NAMES.get(tc, str(tc)), "class": drain, "source": "awc, openlandmap",
                     "interpretation": f"Holds about {AWC_MM_PER_M.get(tc, 'n/a')} mm of plant-available water per metre; {drain}.", "scored": True})
    if sl is not None:
        label, _ = _band(sl, SLOPE_CLASSES)
        rows.append({"id": "slope", "name": "Slope", "value": f"{round(sl, 1)} %", "class": label, "source": "aislus", "interpretation": f"AIS&LUS class: {label}.", "scored": True})
    return {"properties": rows, "not_measurable": ["salinity (electrical conductivity)", "nitrogen, phosphorus, potassium and micronutrients", "soil biology and structure", "compaction and crusting"],
            "advice": "These cannot be sensed from space. Get a soil test (nutrients, pH, organic carbon, salinity) from a KVK or the state soil-testing laboratory; the Soil Health Card scheme provides it.",
            "model_note": CITATIONS["openlandmap"]}


def climate_panel(profile: Dict[str, Any]) -> Dict[str, Any]:
    t = [x for x in (profile.get("monthly_temp_c") or []) if x is not None]
    ai = aridity_index(profile)
    return {"annual_rain_mm": profile.get("annual_rain_mm"), "annual_mean_temp_c": profile.get("annual_mean_temp_c"), "coldest_month_c": min(t) if t else None, "hottest_month_c": max(t) if t else None,
            "aridity": ai, "monthly_rain_mm": profile.get("monthly_rain_mm"), "monthly_temp_c": profile.get("monthly_temp_c"), "monthly_ndvi": profile.get("monthly_ndvi"), "years": profile.get("climate_years"),
            "note": profile.get("climate_note")}


# ----------------------------------------------------------------------------------------------------------- assembly (pure) and orchestration
def assemble_assessment(x: Dict[str, Any]) -> Dict[str, Any]:
    """Build the full assessment from already-fetched inputs. Pure: all network work happens in compute_risk_assessment."""
    err = x.get("errors") or {}; profile = x.get("profile") or {}
    inds = [ind_vhi(x.get("vdi"), err.get("vdi")), ind_ndvi(x.get("baseline"), err.get("baseline")), ind_rain(x.get("prec"), err.get("prec")), ind_rootzone(x.get("rz"), err.get("rz")),
            ind_groundwater(x.get("gw"), err.get("gw"))]
    fam = family_scores(inds); stress = combine(fam); sens = sensitivity(fam); conf = confidence(inds)
    vuln = vulnerability(profile, x.get("gw")) if profile else {"score": None, "class": "unknown", "factors": [], "factors_used": 0, "of": 5}
    risk = risk_class(stress, vuln["score"])
    ok = sorted([i for i in inds if i["status"] == "ok"], key=lambda i: -(i["stress"] or 0))
    drivers = [f"{i['name']}: {i['class']}" for i in ok if (i["stress"] or 0) >= 40][:3]
    calm = [i["name"] for i in ok if (i["stress"] or 0) == 0]
    v1 = x.get("v1") or {}
    return {"as_of": x.get("as_of"), "stress": {"score": stress, "class": stress_level(stress), "families": {f: {"label": FAMILY_LABEL[f], "score": fam[f]} for f in FAMILIES}},
            "vulnerability": vuln, "risk": risk, "confidence": conf, "sensitivity": sens, "indicators": inds,
            "summary": {"drivers": drivers, "no_signal": calm, "unavailable": [f"{i['name']} ({i['reason']})" for i in inds if i["status"] != "ok"]},
            "soil_condition": soil_condition(profile) if profile else None, "climate": climate_panel(profile) if profile else None,
            "legacy": {"risk_score": v1.get("risk_score"), "band": v1.get("band"), "confidence": v1.get("confidence"), "note": "The original 3-indicator score (drought extent, vegetation loss, soil-moisture), kept for alerts and the watchlist."} if v1 else None,
            "errors": err, "citations": CITATIONS, "limitations": LIMITATIONS,
            "methodology": ["Each stress indicator is compared with its own multi-year history and placed in a standard class (US Drought Monitor percentile bands; Kogan VHI classes).",
                            "Classes are scaled to 0-100 stress points: 20 per US Drought Monitor category, 25 per VHI class (a convention).",
                            "Indicators are averaged within each family, then families are averaged with equal weights over the families that are available.",
                            "Land vulnerability is the mean of documented ordinal ratings (water-holding capacity, organic carbon, slope, aridity, groundwater trend).",
                            "Risk class = stress class moved one step up for high vulnerability, one step down for low vulnerability.",
                            "Sensitivity: the stress score is recomputed under four alternative weightings and with each family left out; the range is reported."]}


def json_safe(x: Any) -> Any:
    """Make an assessment safe to send as JSON: NaN and infinity (which JSON cannot carry and browsers reject) become None, tuples become lists. Nothing else changes."""
    if isinstance(x, float): return x if math.isfinite(x) else None
    if isinstance(x, dict): return {k: json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)): return [json_safe(v) for v in x]
    return x


def _safe(fn, *a, **k):
    try:
        return fn(*a, **k), None
    except Exception as e:
        logger.warning(f"risk assessment source failed ({getattr(fn, '__name__', fn)}): {e}")
        return None, str(e)[:200]


def compute_risk_assessment(aoi: Dict[str, Any], as_of: Optional[str] = None, profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Gather every source concurrently (each can fail on its own), then assemble. Earth Engine paths are mock-tested only from the dev sandbox."""
    from ..gee_remote_sensing import compute_vegetation_drought_index
    from . import crop_suitability
    from .baseline import compute_seasonal_baseline
    from .groundwater import compute_groundwater_trend
    from .precipitation import compute_precipitation_context
    from .risk_scoring import compute_risk_score
    from .root_zone import fetch_root_zone, summarize_root_zone
    end = datetime.strptime(as_of, "%Y-%m-%d") if as_of else datetime.utcnow()
    start = max(end - timedelta(days=60), datetime(end.year, 1, 1))
    if (end - start).days < 20: end, start = datetime(end.year - 1, 12, 31), datetime(end.year - 1, 11, 1)
    e, s0, e0 = end.strftime("%Y-%m-%d"), start.strftime("%Y-%m-%d"), as_of or datetime.utcnow().strftime("%Y-%m-%d")
    jobs = {"v1": (compute_risk_score, (), {"aoi": aoi, "as_of": as_of}), "vdi": (compute_vegetation_drought_index, (), {"aoi": aoi, "start_date": s0, "end_date": e}),
            "baseline": (compute_seasonal_baseline, (), {"aoi": aoi, "as_of": e0, "years_back": 8}), "prec": (compute_precipitation_context, (), {"aoi": aoi, "as_of": e0, "recent_days": 90, "years_back": 30}),
            "rz": (lambda **k: summarize_root_zone(fetch_root_zone(**k)), (), {"aoi": aoi, "as_of": e0}), "gw": (compute_groundwater_trend, (), {"aoi": aoi})}
    if profile is None: jobs["profile"] = (crop_suitability.sample_location_profile, (), {"lat": None, "lon": None, "aoi": aoi})
    out: Dict[str, Any] = {"as_of": e0, "errors": {}}
    with ThreadPoolExecutor(max_workers=7) as pool:
        futs = {k: pool.submit(_safe, fn, *a, **kw) for k, (fn, a, kw) in jobs.items()}
        for k, fu in futs.items():
            val, er = fu.result(); out[k] = val
            if er: out["errors"][k] = er
    if profile is not None: out["profile"] = profile
    return assemble_assessment(out)
