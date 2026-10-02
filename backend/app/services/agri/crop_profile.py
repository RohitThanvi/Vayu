"""
crop_profile.py - the crop PROFILE SCHEMA and its defaults.

A crop entry in crop_requirements.CROPS carries the original EcoCrop-style ranges plus OPTIONAL
fields below. Missing optional fields get documented defaults here, so existing entries keep working.

Required (existing):
  id, name, name_hi, season ('kharif'|'rabi'|'perennial'), tags, notes, source, verified
  temp_c   (abs_min, opt_min, opt_max, abs_max)   degC, mean over the crop's season window
  water_mm (abs_min, opt_min, opt_max, abs_max)   mm: deficit side judged on the water-supply basis,
                                                  excess side on the rain the crop is actually exposed to
  ph       (abs_min, opt_min, opt_max, abs_max)
  texture_good / texture_marginal   USDA texture class codes
  oc_min_gkg, slope_max_pct, yield_q_per_ha, mandi_commodity

Optional (new, all defaulted):
  excess_sensitivity    'low' | 'medium' | 'high'   how badly the crop reacts to excess water.
                        default: 'high' if tagged drought_tolerant else 'medium'
  rainfall_seasonality  {'months': [1-12, ...], 'source': str}   months in which the crop needs DRY weather
                        (e.g. flowering / fruit set). Annual excess rain then matters only in proportion to how
                        wet that window is. default: None (annual excess judged on the amount alone)
  water_override_note / ph_override_note ...   citation for any deviation from EcoCrop
"""
from typing import Any, Dict, List

SEASONS = ("kharif", "rabi", "perennial")
RANGE_KEYS = ("temp_c", "water_mm", "ph")
REQUIRED = ("id", "name", "name_hi", "season", "temp_c", "water_mm", "ph", "texture_good", "texture_marginal",
            "oc_min_gkg", "slope_max_pct", "tags")


def normalize_crop(crop: Dict[str, Any]) -> Dict[str, Any]:
    c = dict(crop)
    c["kind"] = "perennial" if c["season"] == "perennial" else "seasonal"
    if c.get("excess_sensitivity") not in ("low", "medium", "high"):
        c["excess_sensitivity"] = "high" if "drought_tolerant" in c.get("tags", ()) else "medium"
    c.setdefault("rainfall_seasonality", None)
    return c


def validate_crop(crop: Dict[str, Any]) -> List[str]:
    """Return a list of schema problems (empty = valid)."""
    problems = [f"missing '{k}'" for k in REQUIRED if k not in crop]
    if problems:
        return problems
    if crop["season"] not in SEASONS:
        problems.append(f"season '{crop['season']}' not in {SEASONS}")
    for k in RANGE_KEYS:
        a, b, c, d = crop[k]
        if not (a <= b <= c <= d):
            problems.append(f"{k} {crop[k]} is not monotonic (abs_min <= opt_min <= opt_max <= abs_max)")
    rs = crop.get("rainfall_seasonality")
    if rs is not None and not (rs.get("source") and rs.get("months") and all(isinstance(m, int) and 1 <= m <= 12 for m in rs["months"])):
        problems.append("rainfall_seasonality needs 'months' (list of 1-12) and a 'source'")
    return problems
