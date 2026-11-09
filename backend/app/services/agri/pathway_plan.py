"""
pathway_plan.py - the PERSONAL numbers in a farmer's guide: a planting plan sized to their plot, and what their own local climate does to the crop's planting window.
Pure Python. Every figure is arithmetic on numbers that are cited in pathway_data.py; nothing here is a prediction of yield or income.
"""
import math
from typing import Any, Dict, List, Optional

from .pathway_data import CROPS

BY_ID = {c["id"]: c for c in CROPS}
M2_PER_ACRE = 4046.8564224
HA_PER_ACRE = 0.40468564224
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MAX_ACRES = 1000.0


def clean_area(area_acres: Optional[float]) -> float:
    """Default 1 acre. Rejects non-finite, zero/negative and absurd values with a plain message."""
    if area_acres is None:
        return 1.0
    a = float(area_acres)
    if not math.isfinite(a) or a <= 0 or a > MAX_ACRES:
        raise ValueError(f"Land area must be more than 0 and at most {int(MAX_ACRES)} acres.")
    return a


def _fmt(v: float) -> str:
    if v >= 1000:
        return f"{v:,.0f}"
    return f"{v:.0f}" if v >= 10 or float(v).is_integer() else f"{v:.1f}"


def _range_text(lo: float, hi: float, unit: str) -> str:
    if unit == "kg" and hi >= 1000:           # show big masses in tonnes
        lo, hi, unit = lo / 1000.0, hi / 1000.0, "tonnes"
    return f"{_fmt(lo)} {unit}" if abs(lo - hi) < 1e-9 else f"{_fmt(lo)} to {_fmt(hi)} {unit}"


def compute_plan(crop_id: str, area_acres: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Plants and materials for the farmer's plot, from cited spacing and per-plant / per-hectare amounts. None for crops with no area-based plan (saffron)."""
    crop = BY_ID[crop_id]; plan = crop.get("plan")
    if not plan:
        return None
    acres = clean_area(area_acres)
    ha = acres * HA_PER_ACRE
    rows: List[Dict[str, Any]] = []
    if plan.get("spacing_m"):
        a, b = plan["spacing_m"]
        n = int(math.floor(acres * M2_PER_ACRE / (a * b)))
        rows.append({"label": f"{plan['plant_noun'].capitalize()} at {a:g} m x {b:g} m", "value": f"about {_fmt(n)}", "basis": "arithmetic", "src": plan["spacing_src"]})
    else:
        n = int(plan["example_units"])
        rows.append({"label": f"Example starter batch ({plan['plant_noun']})", "value": f"{n}", "basis": "example", "src": []})
    for label, lo, hi, unit, src in plan["per_plant"]:
        rows.append({"label": label, "value": _range_text(lo * n, hi * n, unit), "basis": "arithmetic", "src": src})
    for label, lo, hi, unit, src in plan["per_ha"]:
        rows.append({"label": label, "value": _range_text(lo * ha, hi * ha, unit), "basis": "arithmetic", "src": src})
    return {"area_acres": round(acres, 2), "area_ha": round(ha, 3), "count": n, "plant_noun": plan["plant_noun"], "spacing_m": plan.get("spacing_m"), "rows": rows,
            "note": "Sums of the cited per-plant and per-hectare amounts for your plot size. Real needs vary with soil and variety; confirm with your KVK."}


def planting_window(crop_id: str, monthly_rain_mm: Optional[List[Optional[float]]], monthly_temp_c: Optional[List[Optional[float]]]) -> Optional[Dict[str, Any]]:
    """The crop's cited planting months overlaid on THIS location's own climate: average rain and temperature across the window."""
    pm = BY_ID[crop_id].get("planting_months")
    if not pm:
        return None
    ms = pm["months"]
    def avg(series, total=False):
        vals = [series[m - 1] for m in ms if series and len(series) == 12 and series[m - 1] is not None]
        if len(vals) != len(ms):
            return None
        return round(sum(vals) / (1 if total else len(vals)), 1) if not total else round(sum(vals), 0)
    return {"months": ms, "month_names": [MONTH_NAMES[m - 1] for m in ms], "note": pm["note"], "src": pm["src"], "rain_in_window_mm": avg(monthly_rain_mm, total=True),
            "mean_temp_in_window_c": avg(monthly_temp_c), "caution": "The planting window comes from the source's region; your local calendar may differ. Check with your KVK."}
