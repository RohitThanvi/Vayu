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
