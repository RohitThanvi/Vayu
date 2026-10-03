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
  excess_sensitivity    'tolerant' | 'low' | 'medium' | 'high'   how badly the crop reacts to excess water.
                        default: 'high' if tagged drought_tolerant else 'medium'
  rainfall_seasonality  {'months': [1-12, ...], 'source': str}   months in which the crop needs DRY weather
                        (e.g. flowering / fruit set). Annual excess rain then matters only in proportion to how
                        wet that window is. default: None (annual excess judged on the amount alone)
  slope_terraceable_pct   int > slope_max_pct: slopes above the natural limit up to this value are a SOFT terracing requirement
                          (score falls from 1.0 to 0.5 across the band, then to 0 over the next equal band). Needs a source.
  humidity_sensitive      {'source': str}: warm-humid sites carry an UNSCORED "disease pressure not modelled" caveat and lower confidence
  water_override_note / ph_override_note / slope_override_note ...   citation for any deviation from EcoCrop
"""
from typing import Any, Dict, List

SEASONS = ("kharif", "rabi", "zaid", "perennial")
RANGE_KEYS = ("temp_c", "water_mm", "ph")
REQUIRED = ("id", "name", "name_hi", "season", "temp_c", "water_mm", "ph", "texture_good", "texture_marginal",
            "oc_min_gkg", "slope_max_pct", "tags")


def normalize_crop(crop: Dict[str, Any]) -> Dict[str, Any]:
    c = dict(crop)
    c["kind"] = "perennial" if c["season"] == "perennial" else "seasonal"
    if c.get("excess_sensitivity") not in ("tolerant", "low", "medium", "high"):
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
    st = crop.get("slope_terraceable_pct")
    if st is not None and not (isinstance(st, int) and st > crop["slope_max_pct"] and crop.get("slope_override_note")):
        problems.append("slope_terraceable_pct must be an int > slope_max_pct and needs a slope_override_note citing a source")
    hs = crop.get("humidity_sensitive")
    if hs is not None and not hs.get("source"):
        problems.append("humidity_sensitive needs a source")
    for k in ("ph", "water_mm", "slope"):
        if crop.get(f"{k}_override_note") is not None and not str(crop[f"{k}_override_note"]).strip():
            problems.append(f"{k}_override_note is empty")
    return problems
