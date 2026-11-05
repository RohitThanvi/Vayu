"""
crop_stages.py - FAO-56 crop growth stages and crop coefficients (Kc) for the annual field crops Vayu covers. Pure Python, no GEE.

Why: a field's water need depends on WHERE it is in its crop cycle, not only on how dry the soil is. FAO-56 describes a cycle as four
stages (initial, development, mid-season, late season) and a Kc curve that is flat at Kc_ini, rises linearly to Kc_mid, stays there,
then falls linearly to Kc_end (FAO-56 ch. 6, eq. 66). Peak demand is the mid-season; a crop allowed to dry down in the field (low Kc_end)
needs little or no irrigation late in its cycle.

Sources (FAO Irrigation and Drainage Paper 56, Allen et al. 1998, https://www.fao.org/4/x0490e/x0490e0b.htm):
  stage lengths - Table 11, one row per crop (row and region named below);  Kc_ini / Kc_mid / Kc_end - Table 12 (Kc_ini is the crop-group value).
Where Table 12 gives a range, the range is stored and the midpoint is used.

Deliberately NOT covered: chickpea and mustard (Table 12 has Kc values but Table 11 has no stage row for them), rice (flooded paddy has a
different water regime), sugarcane and tree crops (not a single sowing-to-harvest cycle). Better to say "unsupported" than to borrow another crop's stages.
Limit: FAO states the Table 11 lengths are indicative and vary with climate, variety and planting date; local observation should replace them.
"""
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

STAGE_NAMES = ("initial", "development", "mid-season", "late season")
STAGE_PLAIN = {"initial": "Initial (establishment)", "development": "Development (canopy growth)", "mid-season": "Mid-season (peak demand)", "late season": "Late season (maturity)"}
SOURCE = "FAO-56 (Allen et al. 1998) Table 11 stage lengths, Table 12 Kc; https://www.fao.org/4/x0490e/x0490e0b.htm"
CAVEAT = "Stage lengths are FAO's indicative values for one region and variety, not local calendars; use your own observed stage dates where you have them."

# id: (name, (L_ini, L_dev, L_mid, L_late), Kc_ini, (Kc_mid lo, hi), (Kc_end lo, hi), Table 11 row used)
_RAW = {
    "wheat":              ("Wheat", (15, 25, 50, 30), 0.30, (1.15, 1.15), (0.25, 0.40), "Barley/Oats/Wheat, planted November, Central India"),
    "barley":             ("Barley", (15, 25, 50, 30), 0.30, (1.15, 1.15), (0.25, 0.25), "Barley/Oats/Wheat, planted November, Central India"),
    "maize":              ("Maize", (20, 35, 40, 30), 0.30, (1.20, 1.20), (0.35, 0.60), "Maize (grain), planted October, India (dry, cool)"),
    "bajra_pearl_millet": ("Bajra (pearl millet)", (15, 25, 40, 25), 0.30, (1.00, 1.00), (0.30, 0.30), "Millet, planted June, Pakistan"),
    "jowar_sorghum":      ("Jowar (sorghum)", (20, 35, 40, 30), 0.30, (1.00, 1.10), (0.55, 0.55), "Sorghum, planted May/June, USA / Pakistan / Mediterranean"),
    "groundnut":          ("Groundnut", (25, 35, 45, 25), 0.40, (1.15, 1.15), (0.60, 0.60), "Groundnut, dry season, West Africa"),
    "soybean":            ("Soybean", (15, 15, 40, 15), 0.40, (1.15, 1.15), (0.50, 0.50), "Soybeans, planted December, Tropics"),
    "cotton":             ("Cotton", (30, 50, 60, 55), 0.35, (1.15, 1.20), (0.50, 0.70), "Cotton, planted Mar-May, Egypt / Pakistan / California"),
    "potato":             ("Potato", (25, 30, 45, 30), 0.50, (1.15, 1.15), (0.75, 0.75), "Potato, planted November, (semi) arid climate (45-day mid-season variant)"),
    "onion":              ("Onion (dry)", (15, 25, 70, 40), 0.70, (1.05, 1.05), (0.75, 0.75), "Onion (dry), planted April, Mediterranean"),
}
DRY_DOWN_KC_END = 0.45        # FAO-56 ch. 6: Kc_end below 0.45 = crop allowed to senesce and dry in the field


def _mid(r): return round((r[0] + r[1]) / 2.0, 3)


CROPS: Dict[str, Dict[str, Any]] = {
    cid: {"id": cid, "name": n, "stages": dict(zip(STAGE_NAMES, L)), "total_days": sum(L), "kc_ini": ki, "kc_mid": _mid(km), "kc_end": _mid(ke),
          "kc_mid_range": km, "kc_end_range": ke, "dry_down": _mid(ke) < DRY_DOWN_KC_END, "table11_row": row}
    for cid, (n, L, ki, km, ke, row) in _RAW.items()
}


def supported_crops() -> List[Dict[str, Any]]:
    return [{"id": c["id"], "name": c["name"], "total_days": c["total_days"]} for c in CROPS.values()]


def kc_on_day(crop: Dict[str, Any], das: int) -> Optional[float]:
    """Kc on day-after-sowing `das` (1 = first day), FAO-56 eq. 66 piecewise-linear curve. None outside the cycle."""
    s = crop["stages"]; li, ld, lm, ll = (s[k] for k in STAGE_NAMES)
    if das < 1 or das > li + ld + lm + ll:
        return None
    if das <= li:
        return crop["kc_ini"]
    if das <= li + ld:
        return crop["kc_ini"] + (das - li) / ld * (crop["kc_mid"] - crop["kc_ini"])
    if das <= li + ld + lm:
        return crop["kc_mid"]
    return crop["kc_mid"] + (das - li - ld - lm) / ll * (crop["kc_end"] - crop["kc_mid"])


def _parse(d) -> Optional[date]:
    if isinstance(d, date) and not isinstance(d, datetime): return d
    try: return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError): return None


def stage_info(crop_id: Optional[str], sowing_date, as_of=None) -> Dict[str, Any]:
    """Where is this crop in its cycle on `as_of`? Never raises: status says why not."""
    crop = CROPS.get(crop_id or "")
    if crop is None:
        return {"status": "unsupported", "crop_id": crop_id, "note": "No FAO-56 stage data for this crop (supported: " + ", ".join(CROPS) + ")."}
    sow, today = _parse(sowing_date), _parse(as_of) or datetime.utcnow().date()
    if sow is None:
        return {"status": "invalid_sowing_date", "crop_id": crop_id, "note": "Sowing date must be YYYY-MM-DD."}
    das = (today - sow).days + 1
    s, edge, cal = crop["stages"], 0, []
    for name in STAGE_NAMES:
        cal.append({"stage": name, "label": STAGE_PLAIN[name], "start_das": edge + 1, "end_das": edge + s[name],
                    "start_date": (sow + timedelta(days=edge)).isoformat(), "end_date": (sow + timedelta(days=edge + s[name] - 1)).isoformat()})
        edge += s[name]
    base = {"crop_id": crop_id, "crop_name": crop["name"], "sowing_date": sow.isoformat(), "as_of": today.isoformat(), "das": das, "total_days": crop["total_days"],
            "kc_mid": crop["kc_mid"], "dry_down_crop": crop["dry_down"], "calendar": cal, "source": SOURCE, "table11_row": crop["table11_row"], "caveat": CAVEAT}
    if das < 1:
        return {**base, "status": "before_sowing", "note": f"Sowing date is {(-das) + 1} day(s) after the check date."}
    if das > crop["total_days"]:
        return {**base, "status": "after_cycle", "note": f"{das - crop['total_days']} day(s) past the FAO cycle length ({crop['total_days']} d); the stage model no longer applies."}
    cur = next(c for c in cal if c["start_das"] <= das <= c["end_das"])
    kc = kc_on_day(crop, das)
    return {**base, "status": "ok", "stage": cur["stage"], "stage_label": cur["label"], "kc": round(kc, 3), "relative_demand": round(kc / crop["kc_mid"], 2),
            "stage_day": das - cur["start_das"] + 1, "stage_length": s[cur["stage"]], "days_left_in_stage": cur["end_das"] - das}
