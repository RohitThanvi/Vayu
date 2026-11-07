"""
crop_pathways.py - "can this crop grow here, what is missing, and how do I grow it?" for high-value crops, from the sourced knowledge base in pathway_data.py.

Four feasibility tiers (never a bare yes/no):
  1 open_field                  nothing in the climate/soil checks is outside the crop's cited range
  2 open_field_with_help        one or more gaps, all fixable at field level (irrigation, drainage, mulch, shade, amendments)
  3 controlled_environment      the crop is grown in a built environment (a mushroom shed, a chilled saffron room), not by matching the open climate
  4 not_realistic               a blocking gap (for example frost for a frost-sensitive crop) with no field-level fix
Plus "cannot_assess" when the inputs needed to decide are missing.

Honesty rules: monthly-average climate cannot see frost nights or hot spells, so frost-sensitive crops always carry a caution; ranges are the crop's general
ones, not a local variety trial; this is not a prediction of yield or profit. Content basis (cited vs general knowledge) travels with every line.
"""
from typing import Any, Dict, List, Optional

from .pathway_data import CROPS

BY_ID = {c["id"]: c for c in CROPS}
TIER_LABELS = {1: "Open-field suitable", 2: "Open-field with help", 3: "Controlled environment", 4: "Not realistic here", None: "Cannot assess"}
SECTION_TITLES = [("planting_material", "Planting material"), ("land_and_planting", "Land preparation and planting"), ("irrigation", "Irrigation"),
                  ("nutrition_organic", "Nutrition (organic)"), ("pests_diseases_organic", "Pests and diseases (organic and cultural only)"),
                  ("monitoring", "Monitoring"), ("harvest_postharvest", "Harvest, yield and post-harvest"), ("risks_costs", "Risks and costs")]
CHEMICAL_POLICY = ("This guide gives organic and cultural methods only. It does not name or dose pesticides, fungicides or chemical fertilisers; for those, follow your nearest "
                   "Krishi Vigyan Kendra (KVK) or state agricultural university and the label of any registered product.")
BASIS_LABEL = {"cited": "", "general": " (general knowledge, not verified against a source)"}


def list_crops() -> List[Dict[str, Any]]:
    return [{"id": c["id"], "name": c["name"], "category": c["category"], "setting": c["setting"], "difficulty": c["first_timer"]["difficulty"],
             "capital": c["first_timer"]["capital"], "risk": c["first_timer"]["risk"]} for c in CROPS]


def climate_from_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Collapse a sampled location profile (crop_suitability.sample_location_profile) to the few numbers the pathway checks use."""
    t = [v for v in (profile.get("monthly_temp_c") or []) if v is not None]
    return {"tmean_c": (sum(t) / len(t)) if t else profile.get("annual_mean_temp_c"), "coldest_month_c": min(t) if t else None, "hottest_month_c": max(t) if t else None,
            "annual_rain_mm": profile.get("annual_rain_mm"), "ph": profile.get("ph")}


def _check(factor, value, rng, status, note=""):
    return {"factor": factor, "your_value": None if value is None else round(value, 1), "crop_range": rng, "status": status, "note": note}


def assess(crop_id: str, climate: Dict[str, Any], irrigated: bool = False) -> Dict[str, Any]:
    """Tier + the checks that produced it. Never raises for a known crop; unknown inputs become 'unknown' checks."""
    crop = BY_ID.get(crop_id)
    if crop is None:
        raise KeyError(crop_id)
    fit = crop["fit"]
    if crop["setting"] == "controlled":
        return {"tier": 3, "tier_label": TIER_LABELS[3], "checks": [], "triggered": [], "cautions": [],
                "why": f"This crop is grown in {crop['controlled']['what']}, so the open-air climate at your location is not what decides success; skills, capital and running costs are."}
    checks, triggered, cautions, unknown = [], [], [], 0
    cold, hot, rain, ph, tmean = climate.get("coldest_month_c"), climate.get("hottest_month_c"), climate.get("annual_rain_mm"), climate.get("ph"), climate.get("tmean_c")
    by_kind = {g["if"]: g for g in crop["gaps"]}

    def trip(kind, addressed=False, severity=None):
        g = by_kind.get(kind)
        if g:
            triggered.append({**g, "addressed": addressed, "severity": severity or g["severity"]})

    if cold is None:
        checks.append(_check("coldest month mean temperature", None, f">{fit['cold_month_min_c']} C", "unknown")); unknown += 1
    elif cold <= fit["cold_month_min_c"]:
        hard = cold <= fit.get("cold_month_block_c", -999)
        checks.append(_check("coldest month mean temperature", cold, f">{fit['cold_month_min_c']} C", "gap",
                             "hard-freeze territory for this crop (limit is general knowledge)" if hard else "cooler than the crop likes")); trip("frost", severity="blocking" if hard else None)
    else:
        checks.append(_check("coldest month mean temperature", cold, f">{fit['cold_month_min_c']} C", "ok"))
    if hot is None:
        checks.append(_check("hottest month mean temperature", None, f"<{fit['hot_month_max_c']} C", "unknown")); unknown += 1
    elif hot >= fit["hot_month_max_c"]:
        hard = hot >= fit.get("hot_month_block_c", 999)
        checks.append(_check("hottest month mean temperature", hot, f"<{fit['hot_month_max_c']} C", "gap", "extreme heat for this crop (limit is general knowledge)" if hard else "hotter than the crop likes")); trip("heat", severity="blocking" if hard else None)
    else:
        checks.append(_check("hottest month mean temperature", hot, f"<{fit['hot_month_max_c']} C", "ok"))
    lo, hi = fit["rain_mm"]
    if rain is None:
        checks.append(_check("annual rainfall", None, f"{lo}-{hi} mm", "unknown")); unknown += 1
    elif rain < lo:
        checks.append(_check("annual rainfall", rain, f"{lo}-{hi} mm", "gap", "irrigation available: counted as addressed" if irrigated else "needs irrigation")); trip("dry", addressed=bool(irrigated))
    elif rain > hi:
        checks.append(_check("annual rainfall", rain, f"{lo}-{hi} mm", "gap", "more rain than the crop's usual range: drainage and disease management")); trip("wet")
    else:
        checks.append(_check("annual rainfall", rain, f"{lo}-{hi} mm", "ok"))
    plo, phi = fit["ph"]
    if ph is None:
        checks.append(_check("soil pH", None, f"{plo}-{phi}", "unknown", "not available; get a soil test"))
    elif not (plo <= ph <= phi):
        checks.append(_check("soil pH", ph, f"{plo}-{phi}", "gap", "modelled soil pH; confirm with a soil test")); trip("ph")
    else:
        checks.append(_check("soil pH", ph, f"{plo}-{phi}", "ok", "modelled soil pH; confirm with a soil test"))
    if not fit.get("frost_tolerant"):
        cautions.append("Monthly averages can hide cold nights and short heat waves. For this frost-sensitive crop, check the lowest winter temperatures your village has actually seen before you plant.")
    cautions += [t for t, _b, _k in crop.get("site_cautions", [])]
    if tmean is not None and not (fit["tmean_c"][0] <= tmean <= fit["tmean_c"][1]):
        cautions.append(f"Your annual mean temperature ({round(tmean, 1)} C) is outside the crop's usual {fit['tmean_c'][0]}-{fit['tmean_c'][1]} C range.")
    open_gaps = [g for g in triggered if not g["addressed"]]
    if cold is None and hot is None:
        tier, why = None, "Temperature data was not available, so the climate checks cannot be run."
    elif any(g["severity"] == "blocking" for g in open_gaps):
        tier, why = 4, "A blocking gap applies and there is no field-level fix: " + "; ".join(g["do"] for g in open_gaps if g["severity"] == "blocking")
    elif open_gaps:
        tier, why = 2, "Workable with help: " + "; ".join(sorted({g["if"] for g in open_gaps})) + " need attention (see actions below)."
    else:
        tier, why = 1, "Nothing in the climate and soil checks is outside the crop's cited range" + (" (irrigation assumed available)." if irrigated and triggered else ".")
    return {"tier": tier, "tier_label": TIER_LABELS[tier], "checks": checks, "triggered": triggered, "cautions": cautions, "why": why}


def _lines(items, sources):
    out = []
    for text, basis, keys in items:
        out.append({"text": text, "basis": basis, "sources": [{"key": k, "url": sources[k]} for k in keys if k in sources]})
    return out


def build_report(crop_id: str, climate: Optional[Dict[str, Any]] = None, irrigated: bool = False) -> Dict[str, Any]:
    """Full pathway report: tier (when a climate is given), what to do about each gap, the grow-it manual, and the sources used."""
    crop = BY_ID[crop_id]
    verdict = assess(crop_id, climate, irrigated) if climate is not None else None
    guide = {k: _lines(crop["guide"][k], crop["sources"]) for k, _ in SECTION_TITLES}
    used = {s["key"] for lines in guide.values() for ln in lines for s in ln["sources"]} | {k for g in crop["gaps"] for k in g["src"]}
    return {"crop": {"id": crop["id"], "name": crop["name"], "category": crop["category"], "setting": crop["setting"]}, "verdict": verdict, "controlled": crop.get("controlled"),
            "first_timer": crop["first_timer"], "gap_actions": [{**g, "sources": [{"key": k, "url": crop["sources"][k]} for k in g["src"] if k in crop["sources"]]} for g in crop["gaps"]],
            "guide": guide, "sources": {k: v for k, v in crop["sources"].items() if k in used}, "chemical_policy": CHEMICAL_POLICY,
            "disclaimer": "Advisory aid from published sources and general agronomic knowledge, not a guarantee of yield or income and not a substitute for local extension advice. Lines marked 'general knowledge' are not verified against a source."}


def render_markdown(report: Dict[str, Any]) -> str:
    c, f, v = report["crop"], report["first_timer"], report["verdict"]
    L = [f"# {c['name']}: how to grow it", ""]
    L += [f"**For a first-timer:** difficulty {f['difficulty']}/5 · capital needed: {f['capital']} · risk: {f['risk']} · first return: {f['time_to_first_fruit']}", "", f"*{f['why']}*", ""]
    if v:
        L += [f"## Can it grow here? {v['tier_label']}", "", v["why"], ""]
        if v["checks"]:
            L += ["| Check | Your location | Crop's range | Result |", "|---|---|---|---|"] + [f"| {k['factor']} | {k['your_value'] if k['your_value'] is not None else 'n/a'} | {k['crop_range']} | {k['status']} |" for k in v["checks"]] + [""]
        for caution in v["cautions"]:
            L.append(f"- Caution: {caution}")
        L.append("")
    if report.get("controlled"):
        L += ["## What you would need to build", "", f"A controlled setup: {report['controlled']['what']}. {report['controlled']['season']}", ""]
    L += ["## If your conditions are off, here is what to do", ""]
    for g in report["gap_actions"]:
        keys = ",".join(s["key"] for s in g["sources"])
        L.append(f"- **{g['if'].replace('_', ' ')}** ({g['severity']}): {g['do']}{BASIS_LABEL[g['basis']]}" + (f" [{keys}]" if keys else ""))
    L.append("")
    for key, title in SECTION_TITLES:
        L += [f"## {title}", ""]
        for ln in report["guide"][key]:
            keys = ",".join(s["key"] for s in ln["sources"])
            L.append(f"- {ln['text']}{BASIS_LABEL[ln['basis']]}" + (f" [{keys}]" if keys else ""))
        L.append("")
    L += ["## About chemicals", "", report["chemical_policy"], "", "## Sources", ""] + [f"- [{k}] {u}" for k, u in sorted(report["sources"].items(), key=lambda kv: int(kv[0][1:]))] + ["", f"*{report['disclaimer']}*", ""]
    return "\n".join(L)
