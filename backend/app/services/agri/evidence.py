"""
evidence.py - ENVIRONMENTAL EVIDENCE layer. Pure functions, no GEE, no crop knowledge.

Turns a sampled location profile into the evidence the scorers need: annual / seasonal / monthly rainfall,
rainfall distribution (dry months, longest dry run, wet-season concentration), temperature, soil, terrain,
and an inferred drainage class. Anything not measured is recorded in `missing` (UNKNOWN is not UNSUITABLE).
Soil moisture and remote-sensing evidence are NOT sampled by the suitability pipeline yet; they are
carried as explicit None so downstream layers can report them as unavailable instead of silently ignoring them.
"""
from typing import Any, Dict, List, Optional, Tuple

from . import engine_config as cfg
from .crop_requirements import SEASON_MONTHS, TEXTURE_NAMES


def trapezoid(x: Optional[float], a: float, b: float, c: float, d: float) -> Optional[float]:
    """1.0 on [b, c], linear ramps a->b and c->d, 0 outside (a, d)."""
    if x is None:
        return None
    if x <= a or x >= d:
        return 0.0
    if x < b:
        return (x - a) / (b - a)
    if x <= c:
        return 1.0
    return (d - x) / (d - c)


def _season_mean_temp(monthly: List[Optional[float]], months: List[int]) -> Optional[float]:
    vals = [monthly[m - 1] for m in months]
    return None if any(v is None for v in vals) else sum(vals) / len(vals)


def _season_rain(monthly: List[Optional[float]], months: List[int]) -> Optional[float]:
    vals = [monthly[m - 1] for m in months]
    return None if any(v is None for v in vals) else sum(vals)


def dry_month_stats(monthly_rain: List[Optional[float]]) -> Tuple[Optional[int], Optional[int]]:
    """(# months below DRY_MONTH_MM, longest circular run of such months)."""
    if not monthly_rain or any(v is None for v in monthly_rain):
        return None, None
    dry = [v < cfg.DRY_MONTH_MM for v in monthly_rain]
    n = sum(dry)
    if n == 12:
        return 12, 12
    best = run = 0
    for v in dry + dry:                       # doubled list handles the Dec->Jan wrap
        run = run + 1 if v else 0
        best = max(best, run)
    return n, min(best, 12)


def wet_season_share(monthly_rain: List[Optional[float]]) -> Optional[float]:
    """Share of annual rain falling in the wettest 4 consecutive months (1.0 = fully monsoonal)."""
    if not monthly_rain or any(v is None for v in monthly_rain):
        return None
    tot = sum(monthly_rain)
    if tot <= 0:
        return None
    best = max(sum(monthly_rain[(i + k) % 12] for k in range(4)) for i in range(12))
    return best / tot


def classify_drainage(slope_pct: Optional[float], texture_class: Optional[int]) -> Dict[str, Any]:
    """Infer drainage from slope + texture (proxies, flagged as inferred)."""
    if slope_pct is None and texture_class is None:
        return {"class": "unknown", "basis": "no slope or texture data", "inferred": False}
    free = texture_class in cfg.FREE_DRAINING_TEXTURES
    fine = texture_class in cfg.POOR_DRAINING_TEXTURES
    if (slope_pct is not None and slope_pct >= cfg.DRAINAGE_SLOPE_GOOD_PCT) or free:
        why = []
        if slope_pct is not None and slope_pct >= cfg.DRAINAGE_SLOPE_GOOD_PCT:
            why.append(f"slope {slope_pct:.1f}% sheds water")
        if free:
            why.append(f"{TEXTURE_NAMES[texture_class].lower()} drains freely")
        return {"class": "good", "basis": "; ".join(why), "inferred": True}
    if fine and slope_pct is not None and slope_pct < cfg.DRAINAGE_SLOPE_POOR_PCT:
        return {"class": "poor", "basis": f"flat ({slope_pct:.1f}%) and {TEXTURE_NAMES[texture_class].lower()} texture", "inferred": True}
    return {"class": "moderate", "basis": "neither clearly free-draining nor clearly waterlogged", "inferred": True}


def build_evidence(profile: Dict[str, Any]) -> Dict[str, Any]:
    mt = profile.get("monthly_temp_c") or [None] * 12
    mr = profile.get("monthly_rain_mm") or [None] * 12
    dry, run = dry_month_stats(mr)
    ev = {
        "monthly_temp_c": mt, "monthly_rain_mm": mr,
        "annual_rain_mm": profile.get("annual_rain_mm"),
        "annual_mean_temp_c": profile.get("annual_mean_temp_c"),
        "dry_months": dry, "longest_dry_run_months": run, "wet_season_share": wet_season_share(mr),
        "ph": profile.get("ph"), "texture_class": profile.get("texture_class"),
        "texture_name": profile.get("texture_name"), "organic_carbon_gkg": profile.get("organic_carbon_gkg"),
        "slope_pct": profile.get("slope_pct"),
        "drainage": classify_drainage(profile.get("slope_pct"), profile.get("texture_class")),
        "soil_moisture": None, "remote_sensing": None,        # not sampled by this pipeline (yet)
    }
    ev["missing"] = [k for k in ("annual_rain_mm", "ph", "texture_class", "organic_carbon_gkg", "slope_pct")
                     if ev[k] is None] + (["monthly_rain_mm"] if dry is None else []) + \
                    (["monthly_temp_c"] if any(v is None for v in mt) else [])
    return ev
