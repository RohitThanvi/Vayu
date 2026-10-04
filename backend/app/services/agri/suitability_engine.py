"""
suitability_engine.py - layered crop-suitability engine (pure Python, no GEE).

  LOCATION/CONTEXT -> ENVIRONMENTAL EVIDENCE (evidence.py) -> CROP PROFILE (crop_profile.py)
      -> FACTOR SCORERS (here) -> CONSTRAINT ENGINE (hard / soft / unknown)
      -> raw score -> CALIBRATION (calibration.py) -> RANKING -> EXPLANATION

Key design rules (see docs/AGRI_SUITABILITY_ENGINE.md):
  * HARD constraint = absolute survival limit (temperature). Only a hard constraint can make a crop "unsuitable".
  * SOFT limitation = reduces the score, floored at SOFT_FLOOR; never zeroes a crop on its own.
  * UNKNOWN evidence = factor omitted + confidence reduced; never counted as unsuitable.
  * Water is three separate questions: deficit (irrigation-aware), excess (drainage-aware) and rainfall
    seasonality (dry-season length a crop needs). Rainfall AMOUNT above a ceiling is only a proxy for
    waterlogging risk, so it is softened by drainage evidence instead of being a cliff to zero.
  * The explanation is generated from the factors that actually reduced the score.
"""
from typing import Any, Dict, List, Optional

from . import engine_config as cfg
from .calibration import Calibrator
from .crop_profile import normalize_crop
from .crop_requirements import SEASON_MONTHS, TEXTURE_NAMES
from .crop_seasons import GROWING_SEASONS, SOURCE as SEASON_SOURCE
from .evidence import _season_mean_temp, _season_rain, build_evidence, trapezoid
from .water_balance import MAI_FULL, MAI_ZERO, window_balance

LABELS = {"temperature": "Temperature", "water": "Rainfall / water supply", "ph": "Soil pH",
          "texture": "Soil texture", "organic_carbon": "Organic carbon", "slope": "Slope"}
SEASON_LABELS = {"kharif": "Kharif (Jun-Oct)", "rabi": "Rabi (Nov-Mar)", "zaid": "Zaid / summer (Mar-May)", "perennial": "Year-round"}
SEASON_SHORT = {"kharif": "Kharif", "rabi": "Rabi", "zaid": "Summer (zaid)", "perennial": "Year-round"}
COMPONENT_LABELS = {"deficit": "Rainfall deficit", "excess": "Excess rainfall", "seasonality": "Dry-season length"}
_calibrator: Optional[Calibrator] = None


def get_calibrator() -> Calibrator:
    global _calibrator
    if _calibrator is None:
        _calibrator = Calibrator.load()
    return _calibrator


def _fmt(v, nd=0, missing="n/a"):
    """Number -> text; never raises on None (messages must not be able to break the endpoint)."""
    return missing if v is None else f"{float(v):.{nd}f}"


def _balance_text(b: Dict[str, Any]) -> str:
    if not b.get("demand_mm"):
        return (f"{_fmt(b['window_rain_mm'])} mm rain + ~{_fmt(b['stored_mm'])} mm stored soil water, but essentially no evaporative demand in this window "
                f"(too cold for active growth)")
    return (f"{_fmt(b['window_rain_mm'])} mm rain + ~{_fmt(b['stored_mm'])} mm stored soil water = ~{_fmt(b['supply_mm'])} mm vs "
            f"~{_fmt(b['demand_mm'])} mm crop demand (PET x Kc), moisture index {_fmt(b['mai'], 2)}")


def _r(v, nd=2):
    return None if v is None else round(float(v), nd)


def _ramp_lo(x, a, b):
    return None if x is None else (1.0 if x >= b else (0.0 if x <= a else (x - a) / (b - a)))


def _ramp_hi(x, c, d):
    return None if x is None else (1.0 if x <= c else (0.0 if x >= d else (d - x) / (d - c)))


# ═══════════════════════════ water model (deficit / excess / seasonality) ═══════════════════════════
def water_model(crop: Dict[str, Any], ev: Dict[str, Any], irrigation: bool, season: Optional[str] = None) -> Dict[str, Any]:
    season = season or crop["season"]
    in_season = _season_rain(ev["monthly_rain_mm"], SEASON_MONTHS[season])
    annual = ev.get("annual_rain_mm")
    # DEFICIT side: supply basis. Kharif is rainfed in-season; rabi/perennial draw on stored soil moisture and
    # irrigation recharged by the whole year's rain, so annual rainfall is the closer proxy.
    # Zaid (Mar-Jun) is the dry pre-monsoon window: crops live on irrigation, so only in-window rain counts as free supply.
    supply, basis = (in_season, "in-season rainfall") if season == "kharif" else \
                    (annual, "annual rainfall (EcoCrop envelope; dry-window crops are also checked against the soil-water balance)")
    # EXCESS side: the rain the crop is actually exposed to. A Nov-Mar crop never sees the monsoon;
    # perennials live through it.
    exposure = annual if season == "perennial" else (in_season if in_season is not None else supply)
    wa, wb, wc, wd = crop["water_mm"]

    lo = _ramp_lo(supply, wa, wb)
    hi_raw = _ramp_hi(exposure, wc, wd)
    irr = (lambda x: None if x is None else (1.0 - 0.5 * (1.0 - x) if irrigation else x))
    deficit = irr(lo)
    # DRY-WINDOW crops (rabi, zaid): judge the deficit on a soil-water balance (in-window rain + soil water stored after the monsoon vs
    # Kc * PET), not on annual rainfall. For rabi the EcoCrop annual-rainfall envelope still applies as well (the lower of the two).
    balance = None
    wbal = ev.get("water_balance")
    if season in ("rabi", "zaid") and wbal and not any(v is None for v in ev["monthly_rain_mm"]):
        balance = window_balance(wbal, ev["monthly_rain_mm"], SEASON_MONTHS[season])
        balance["awc_mm"] = wbal["awc_mm"]
        bal_score = balance["rainfed_score"]
        # The crop's own water-intensity still comes from its EcoCrop range (annual envelope); the balance adds WHEN the water is available.
        deficit = irr(bal_score) if lo is None else min(irr(lo), irr(bal_score))
        supply, basis = balance["supply_mm"], "in-window rain + stored soil water (water balance)"
        # keep `lo` (the envelope) for the rainfall-amount component; the balance is reported separately

    # RAIN-SENSITIVE WINDOW (e.g. mango flowering): how dry is the window the crop needs dry?
    seas, window_rain = None, None
    rs = crop.get("rainfall_seasonality")
    if rs:
        window_rain = _season_rain(ev["monthly_rain_mm"], rs["months"])
        if window_rain is not None:
            full = len(rs["months"]) * cfg.DRY_MONTH_MM
            seas = _ramp_hi(window_rain, full, full * cfg.DRY_WINDOW_ZERO_FACTOR)

    # EXCESS: amount above the ceiling is a PROXY for waterlogging/disease risk, so its strength depends on drainage
    # evidence and crop sensitivity - and, where the crop declares a sensitive window, on how wet that window is
    # (annual rain only matters to the extent it falls when the crop is vulnerable).
    drain = ev["drainage"]["class"]
    k = cfg.EXCESS_STRENGTH_BY_DRAINAGE[drain] + cfg.EXCESS_SENSITIVITY_ADJUST[crop["excess_sensitivity"]]
    k = min(1.0, max(cfg.EXCESS_STRENGTH_FLOOR[crop["excess_sensitivity"]], k))
    if seas is not None:
        k *= (1.0 - seas)
    excess = None if hi_raw is None else 1.0 - k * (1.0 - hi_raw)

    comps = {n: v for n, v in (("deficit", deficit), ("excess", excess), ("seasonality", seas)) if v is not None}
    score = min(comps.values()) if comps else None
    driver = min(comps, key=lambda n: comps[n]) if comps else None          # dict order breaks ties: deficit first
    if supply is None and exposure is None:
        status = "unknown"
    elif exposure is not None and exposure > wc:
        status = "excess_rain"
    elif balance is not None and balance["mai"] is not None:
        status = "rainfed_ok" if balance["mai"] >= cfg.RAINFED_OK_MAI else ("supplemental_irrigation" if balance["mai"] >= cfg.SUPPLEMENTAL_MAI else "irrigation_required")
    elif supply is not None and supply >= wb:
        status = "rainfed_ok"
    elif supply is not None and supply >= wa:
        status = "supplemental_irrigation"
    else:
        status = "irrigation_required"
    return {"score": score, "components": comps, "driver": driver, "status": status, "supply_mm": supply,
            "exposure_mm": exposure, "window_rain_mm": window_rain, "balance": balance, "basis": basis, "lo": lo, "hi_raw": hi_raw, "strength": k,
            "drainage": ev["drainage"], "need": f"{wb}-{wc} mm", "ceiling": (wc, wd)}


# ═════════════════════════════════════ factor scorers ═════════════════════════════════════
def _score_factors(crop: Dict[str, Any], ev: Dict[str, Any], irrigation: bool):
    f: Dict[str, Dict[str, Any]] = {}
    ta, tb, tc, td = crop["temp_c"]
    window = SEASON_MONTHS[crop["season"]]
    t = _season_mean_temp(ev["monthly_temp_c"], window)
    ts = trapezoid(t, ta, tb, tc, td)
    f["temperature"] = {"raw": ts, "value": _r(t, 1), "unit": "°C", "need": f"{tb}-{tc} °C", "window": window,
                        "hard": t is not None and (t <= ta or t >= td), "limits": (ta, td)}

    w = water_model(crop, ev, irrigation)
    f["water"] = {"raw": w["score"], "value": _r(w["supply_mm"], 0), "unit": "mm", "need": w["need"], "model": w}

    ps = trapezoid(ev.get("ph"), *crop["ph"])
    f["ph"] = {"raw": ps, "value": ev.get("ph"), "unit": "", "need": f"{crop['ph'][1]}-{crop['ph'][2]}"}

    tcx = ev.get("texture_class")
    txs = None if tcx is None else (1.0 if tcx in crop["texture_good"] else (0.6 if tcx in crop["texture_marginal"] else 0.25))
    f["texture"] = {"raw": txs, "value": ev.get("texture_name"), "unit": "",
                    "need": ", ".join(TEXTURE_NAMES[c] for c in crop["texture_good"][:3]) + ", ..."}

    oc = ev.get("organic_carbon_gkg")
    f["organic_carbon"] = {"raw": None if oc is None else 0.5 + 0.5 * min(1.0, oc / crop["oc_min_gkg"]),
                           "value": oc, "unit": "g/kg", "need": f">= {crop['oc_min_gkg']} g/kg"}
    sp, sm = ev.get("slope_pct"), crop["slope_max_pct"]
    st = crop.get("slope_terraceable_pct")
    if sp is None:
        slope_raw = None
    elif sp <= sm:
        slope_raw = 1.0
    elif st:                                   # slopes above the natural limit are a SOFT terracing requirement (sourced per crop)
        edge = cfg.TERRACE_SCORE_AT_LIMIT
        slope_raw = (1.0 - (1.0 - edge) * (sp - sm) / (st - sm)) if sp <= st else max(0.0, edge * (1.0 - (sp - st) / (st - sm)))
    else:
        slope_raw = max(0.0, 1.0 - (sp - sm) / sm)
    f["slope"] = {"raw": slope_raw, "value": sp, "unit": "%", "need": f"<= {sm}%" + (f" (terraceable to {st}%)" if st else ""),
                  "terracing": bool(st and sp is not None and sm < sp <= st)}

    for key, x in f.items():                       # constraint engine: hard / soft / unknown
        if x["raw"] is None:
            x["severity"], x["effective"] = "unknown", None
        elif key in cfg.HARD_FACTORS and x.get("hard"):
            x["severity"], x["effective"] = "hard", 0.0
        else:
            x["severity"], x["effective"] = "soft", max(x["raw"], cfg.SOFT_FLOOR)
    return f


# ═════════════════════════════════════ explanation engine ═════════════════════════════════════
def _message(key: str, x: Dict[str, Any], crop: Dict[str, Any], irrigation: bool, comp: Optional[str]) -> str:
    v, u, need = x["value"], x["unit"], x["need"]
    if key == "temperature":
        ta, td = x["limits"]
        win = f" ({SEASON_SHORT[crop['season']]} window mean)" if crop["season"] != "perennial" else " (annual mean)"
        if x["severity"] == "hard":
            return f"Temperature {v} °C{win} is outside the range this crop can survive ({ta}-{td} °C)."
        return f"Temperature {v} °C{win} is {'above' if v is not None and v > x['limits'][0] + (td - ta) / 2 else 'below'} the optimum {need}."
    if key == "water":
        m = x["model"]
        if comp == "deficit" and m.get("balance"):
            b = m["balance"]
            return ("Water balance: " + _balance_text(b) + "; "
                    + ("irrigation is available and partly offsets the shortfall." if irrigation else "no irrigation, so a rainfed crop would be water-stressed."))
        if comp == "deficit":
            return (f"Rainfall deficit: {_r(m['supply_mm'], 0)} mm ({m['basis']}) vs ~{crop['water_mm'][1]} mm needed; "
                    + ("irrigation is available and partly offsets this." if irrigation else "no irrigation, so the shortfall counts in full."))
        if comp == "excess":
            wc, wd = m["ceiling"]
            return (f"Excess rainfall: {_r(m['exposure_mm'], 0)} mm vs ceiling {wc}-{wd} mm. Drainage looks {m['drainage']['class']} "
                    f"({m['drainage']['basis']}), so this is a soft limit (strength {_fmt(m['strength'], 2)}), not a hard cut-off.")
        if comp == "seasonality":
            rs = crop["rainfall_seasonality"]
            return (f"Rain falls in the months this crop needs dry: {_r(m['window_rain_mm'], 0)} mm across months {rs['months']} "
                    f"(dry means < {cfg.DRY_MONTH_MM:.0f} mm/month).")
    if key == "slope" and x.get("terracing"):
        return f"Slope {v}% is above the natural limit for this crop; it needs bench terracing (workable up to ~{crop['slope_terraceable_pct']}%), which adds cost."
    label = LABELS[key]
    return f"{label} {v}{(' ' + u) if u else ''} vs needed {need}."


def _ok_message(key: str, x: Dict[str, Any], crop: Dict[str, Any]) -> str:
    if key == "water":
        m = x["model"]
        if m.get("balance"):
            b = m["balance"]
            parts = [_balance_text(b)]
        else:
            parts = [f"water supply {_r(m['supply_mm'], 0)} mm vs ~{crop['water_mm'][1]} mm needed"]
        if m["status"] == "excess_rain":
            if m["window_rain_mm"] is not None:
                parts.append(f"rain above the generic {m['ceiling'][0]} mm ceiling is tolerated because the crop's rain-sensitive months are dry ({_r(m['window_rain_mm'], 0)} mm)")
            else:
                parts.append("rainfall above the generic ceiling is tolerated here")
        return "Water conditions compatible (" + "; ".join(parts) + ")"
    return f"{LABELS[key]} compatible ({x['value']}{(' ' + x['unit']) if x['unit'] else ''}; needs {x['need']})"


def _explain(factors, crop, irrigation):
    limits, ok, unknown = [], [], []
    for key, x in factors.items():
        if x["severity"] == "unknown":
            unknown.append(key); continue
        comp = x["model"]["driver"] if key == "water" else None
        red = 1.0 - x["effective"]
        if red >= 0.05:
            limits.append({"factor": key, "component": comp, "kind": x["severity"], "reduction": round(red, 2),
                           "score": round(x["effective"], 2), "label": COMPONENT_LABELS.get(comp, LABELS[key]),
                           "message": _message(key, x, crop, irrigation, comp)})
        else:
            ok.append({"factor": key, "message": _ok_message(key, x, crop)})
    limits.sort(key=lambda d: (-d["reduction"], d["factor"]))
    lines = [("✗ " if l["kind"] == "hard" else "⚠ ") + l["message"] for l in limits] + \
            ["✓ " + o["message"] for o in ok] + [f"? {LABELS[k]}: no data (not counted against the crop)" for k in unknown]
    return limits, lines, unknown


def _amendments(crop, ev, factors, irrigation) -> List[str]:
    tips: List[str] = []
    m = factors["water"]["model"]
    if m.get("balance") and m["status"] in ("supplemental_irrigation", "irrigation_required"):
        b = m["balance"]; short = max(0.0, b["demand_mm"] - b["supply_mm"])
        tips.append(f"Plan about {round(short)} mm of irrigation over the season (demand ~{round(b['demand_mm'])} mm vs ~{round(b['supply_mm'])} mm from rain and stored soil water)."
                    if irrigation else f"A rainfed crop would be ~{round(short)} mm short (demand ~{round(b['demand_mm'])} mm vs ~{round(b['supply_mm'])} mm available); it needs irrigation.")
    elif m["status"] in ("supplemental_irrigation", "irrigation_required") and m["supply_mm"] is not None:
        short = crop["water_mm"][1] - m["supply_mm"]
        if irrigation:
            tips.append(f"Plan about {round(short)} mm of irrigation over the season (rain supplies ~{round(m['supply_mm'])} mm).")
        else:
            tips.append(f"Rain (~{round(m['supply_mm'])} mm) is short of what this crop needs (~{crop['water_mm'][1]}+ mm); rainfed yields will be poor without irrigation.")
    if m["status"] == "excess_rain":
        tips.append(f"Heavy rain for this crop; drainage looks {m['drainage']['class']}. Ensure field drainage to limit waterlogging and disease.")
    ph = ev.get("ph")
    if ph is not None and ph > crop["ph"][2]:
        tips.append("Soil is more alkaline than this crop prefers; gypsum and organic matter can help. Confirm with a soil test first.")
    if ph is not None and ph < crop["ph"][1]:
        tips.append("Soil is more acidic than this crop prefers; liming can help. Confirm with a soil test first.")
    oc = ev.get("organic_carbon_gkg")
    if oc is not None and oc < crop["oc_min_gkg"]:
        tips.append("Low organic carbon: add compost / farmyard manure or green manure to build fertility and water holding.")
    tx = factors["texture"]["raw"]
    if tx is not None and tx < 1.0:
        tips.append("Soil texture is not ideal for this crop; mulching, organic matter and drip/frequent light irrigation (sandy) or drainage (clayey) can offset it.")
    return tips


# ═════════════════════════════════════ public API ═════════════════════════════════════
def category_of(score: float, hard: bool) -> str:
    if hard:
        return "unsuitable"
    return "high" if score >= cfg.CATEGORY_HIGH else "moderate" if score >= cfg.CATEGORY_MODERATE else \
           "low" if score >= cfg.CATEGORY_LOW else "very_low"


def score_crop_v2(crop_in: Dict[str, Any], profile: Dict[str, Any], irrigation: bool,
                  ev: Optional[Dict[str, Any]] = None, calibrator: Optional[Calibrator] = None,
                  season: Optional[str] = None) -> Dict[str, Any]:
    """Score one crop in ONE season window (default: the crop's own tagged season). Perennials always use the whole year."""
    crop = normalize_crop(crop_in)
    if season and crop["kind"] == "seasonal":
        crop["season"] = season
    ev = ev or build_evidence(profile)
    cal = calibrator or get_calibrator()
    f = _score_factors(crop, ev, irrigation)
    scored = {k: x["effective"] for k, x in f.items() if x["effective"] is not None}
    if not scored:
        return {"crop_id": crop["id"], "score": None, "rating": "no_data", "category": "no_data",
                "factors": {k: {"score": None, "value": x["value"], "unit": x["unit"], "need": x["need"]} for k, x in f.items()}}
    hard = [k for k, x in f.items() if x["severity"] == "hard"]
    raw = 0.0 if hard else min(scored.values())
    calibrated = 0.0 if hard else max(0.0, min(1.0, cal.apply(raw, crop["season"])))
    cat = category_of(calibrated, bool(hard))
    limits, lines, unknown = _explain(f, crop, irrigation)
    caveats = []
    hs = crop.get("humidity_sensitive")
    if hs and ev.get("annual_rain_mm") is not None and ev["annual_rain_mm"] >= cfg.HUMID_SITE_RAIN_MM:
        caveats.append({"factor": "humidity_disease", "scored": False,
                        "message": f"High-rainfall site ({_fmt(ev['annual_rain_mm'])} mm/yr): humidity / disease pressure is NOT modelled and may reduce real-world suitability.",
                        "source": hs["source"]})
        lines = lines + ["? " + caveats[0]["message"]]
    primary = limits[0] if limits else None
    # confidence: completeness / quality of the evidence behind this number (heuristic index, not a statistical interval)
    sa = ev.get("slope_all_deg")
    if sa is not None and sa >= cfg.MOUNTAIN_AOI_SLOPE_DEG:
        caveats.append({"factor": "heterogeneous_terrain", "scored": False, "source": "assumption (engine_config.MOUNTAIN_AOI_SLOPE_DEG)",
                        "message": (f"Mountainous AOI (mean slope {_fmt(sa, 0)} deg): temperature and rainfall come from a ~5-11 km grid averaged over a wide elevation range, "
                                    "but crops are grown in valleys and on terraces, so warm-season crops are likely under-rated here. Draw a smaller AOI over cultivated land for a more representative result.")})
        lines = lines + ["? " + caveats[-1]["message"]]
    bal = f["water"]["model"].get("balance")
    if bal and bal["supply_mm"] > 0 and bal["stored_mm"] / bal["supply_mm"] >= cfg.STORED_SHARE_FOR_DEPTH_CAVEAT:
        caveats.append({"factor": "soil_depth", "scored": False, "source": "assumption (water_balance.ROOT_ZONE_M)",
                        "message": "Stored soil water assumes a 1.0 m root zone; soil depth is not measured, so on shallow soils (e.g. laterite) rainfed dry-season estimates are optimistic."})
        lines = lines + ["? " + caveats[-1]["message"]]
    conf = 1.0 - cfg.CONF_PENALTY_UNKNOWN_FACTOR * len(unknown) - cfg.CONF_PENALTY_UNMODELLED_RISK * sum(1 for c in caveats if c["factor"] == "humidity_disease") \
           - (cfg.CONF_PENALTY_ASSUMED_SOIL_DEPTH if any(c["factor"] == "soil_depth" for c in caveats) else 0.0) \
           - (cfg.CONF_PENALTY_HETEROGENEOUS_TERRAIN if any(c["factor"] == "heterogeneous_terrain" for c in caveats) else 0.0)
    if ev["drainage"]["inferred"] and f["water"]["model"]["hi_raw"] is not None and f["water"]["model"]["hi_raw"] < 1.0:
        conf -= cfg.CONF_PENALTY_INFERRED_DRAINAGE
    if not crop.get("verified", True):
        conf -= cfg.CONF_PENALTY_UNVERIFIED_CROP
    conf = round(max(cfg.CONF_MIN, conf), 2)

    wm_r = water_model(crop, ev, False)["score"]
    wm_i = water_model(crop, ev, True)["score"]
    m = f["water"]["model"]
    rainfall_amount = None if (m["lo"] is None and m["hi_raw"] is None) else min(v for v in (m["lo"], m["hi_raw"]) if v is not None)
    soil = [f[k]["effective"] for k in ("ph", "texture", "organic_carbon") if f[k]["effective"] is not None]
    clim = [v for v in (f["temperature"]["effective"], rainfall_amount) if v is not None]
    headroom = sum(scored.values()) / len(scored)
    components = {
        "temperature_score": _r(f["temperature"]["effective"]), "rainfall_score": _r(rainfall_amount),
        "water_score": _r(f["water"]["effective"]), "irrigation_score": _r(m["components"].get("deficit")),
        "season_score": _r(m["components"].get("seasonality")), "soil_score": _r(min(soil) if soil else None),
        "terrain_score": _r(f["slope"]["effective"]), "climate_score": _r(min(clim) if clim else None),
        "remote_sensing_score": None, "risk_penalty": _r(1.0 - raw), "raw_score": _r(raw), "calibrated_score": _r(calibrated),
    }
    legacy_factors = {}
    for k, x in f.items():
        d = {"score": _r(x["raw"]), "effective_score": _r(x["effective"]), "severity": x["severity"],
             "value": x["value"], "unit": x["unit"], "need": x["need"]}
        if k == "water":
            d.update({"status": m["status"], "basis": m["basis"],
                      "components": {n: _r(v) for n, v in m["components"].items()}, "driver": m["driver"],
                      "drainage": m["drainage"], "excess_strength": round(m["strength"], 2), "window_rain_mm": _r(m["window_rain_mm"], 0),
                      "balance": None if not m["balance"] else {k: _r(v, 2) for k, v in m["balance"].items()}})
        legacy_factors[k] = d
    lim_key = primary["factor"] if primary else "none"
    return {
        "crop_id": crop["id"], "name": crop["name"], "name_hi": crop["name_hi"], "season": crop["season"],
        "tags": crop["tags"], "notes": crop["notes"],
        "score": round(calibrated, 2), "rating": "unsuitable" if cat == "unsuitable" else ("low" if cat == "very_low" else cat),
        "category": cat, "confidence": conf, "raw_score": round(raw, 2), "calibrated_score": round(calibrated, 2),
        "limiting_factor": lim_key,
        "limiting_label": primary["label"] if primary else "No significant limitation",
        "limiting_factors": {"primary": primary, "secondary": limits[1:3]},
        "constraints": {"hard": [{"factor": k, "message": next(l["message"] for l in limits if l["factor"] == k)} for k in hard],
                        "soft": [{"factor": l["factor"], "component": l["component"], "reduction": l["reduction"]} for l in limits if l["kind"] == "soft"],
                        "unknown": unknown},
        "evidence_summary": lines, "caveats": caveats, "components": components,
        "factors": legacy_factors, "water_status": m["status"], "amendments": _amendments(crop, ev, f, irrigation),
        "ablation_inputs": {"temperature": _r(f["temperature"]["raw"], 3), "temperature_hard": bool(hard),
                            "rainfall_amount": _r(rainfall_amount, 3), "water_rainfed": _r(wm_r, 3), "water_irrigated": _r(wm_i, 3),
                            "ph": _r(f["ph"]["raw"], 3), "texture": _r(f["texture"]["raw"], 3),
                            "organic_carbon": _r(f["organic_carbon"]["raw"], 3), "slope": _r(f["slope"]["raw"], 3)},
        "_headroom": round(headroom, 4),
    }


def rank_crops(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deterministic, tie-aware ranking on CALIBRATED scores. Order: calibrated score, then headroom (mean of the
    other factor scores), then name. Crops with equal (score, headroom) share a rank and are flagged tied."""
    valid = [r for r in results if r["score"] is not None]
    ranked = sorted(valid, key=lambda r: (-r["calibrated_score"], -r["_headroom"], r["name"]))
    prev, rank = None, 0
    for i, r in enumerate(ranked, 1):
        key = (r["calibrated_score"], r["_headroom"])
        if key != prev:
            rank, prev = i, key
        r["rank"] = rank
        r["components"]["final_rank"] = rank
    for r in ranked:
        r["tied_with"] = sum(1 for o in ranked if o is not r and o["rank"] == r["rank"])
        del r["_headroom"]
    return ranked


# ═════════════════════════════════════ multi-season layer ═════════════════════════════════════
def _season_entry(r: Dict[str, Any], season: str, share: Optional[float], ev: Dict[str, Any], primary: bool) -> Dict[str, Any]:
    months = SEASON_MONTHS[season]
    p = r["limiting_factors"]["primary"]
    return {"score": r["score"], "category": r["category"], "confidence": r["confidence"], "months": months,
            "window_temp_c": r["factors"]["temperature"]["value"], "window_rain_mm": _r(_season_rain(ev["monthly_rain_mm"], months), 0),
            "limiting_label": r["limiting_label"], "message": (p["message"] if p else "No significant limitation."),
            "hard_limit": bool(r["constraints"]["hard"]), "observed_area_share": share, "is_primary_season": primary}


def _highlight(name: str, entries: Dict[str, Dict[str, Any]], best: str, not_grown: Dict[str, float]) -> str:
    """Plain-language seasonal summary generated from the per-season results."""
    b = entries[best]
    if best == "perennial":
        return f"Perennial crop: judged on the whole year. {b['category'].replace('_', ' ').capitalize()} ({b['score']})."
    parts = [f"Grows best in {SEASON_SHORT[best]} ({b['category'].replace('_', ' ')}, {b['score']})."]
    for s, e in entries.items():
        if s == best: continue
        why = "" if e["limiting_label"] == "No significant limitation" else f": {e['limiting_label'].lower()}"
        if e["hard_limit"]:
            parts.append(f"{SEASON_SHORT[s]}: unsuitable{why} (temperature {e['window_temp_c']} °C).")
        else:
            parts.append(f"{SEASON_SHORT[s]}: {e['category'].replace('_', ' ')} ({e['score']}){why}.")
    if len(entries) == 1:
        parts.append("Observed practice: grown in this season only.")
    if not_grown:
        parts.append("Not grown in practice: " + ", ".join(f"{SEASON_SHORT[s]} ({v:.0%} of national area)" for s, v in not_grown.items()) + ".")
    return " ".join(parts)


def score_crop_seasons(crop_in: Dict[str, Any], profile: Dict[str, Any], irrigation: bool, ev: Optional[Dict[str, Any]] = None,
                       calibrator: Optional[Calibrator] = None, season_request: Optional[str] = None) -> Dict[str, Any]:
    """Score a crop in every season it is actually grown, pick the best, and keep the per-season results.
    Eligible seasons come from observed practice (GROWING_SEASONS, DES area by season); suitability within a season is physical."""
    crop = normalize_crop(crop_in)
    ev = ev or build_evidence(profile)
    cal = calibrator or get_calibrator()
    info = GROWING_SEASONS.get(crop["id"])
    if crop["kind"] == "perennial":
        eligible, shares, primary = ["perennial"], {}, "perennial"
    elif info:
        eligible, shares, primary = list(info["seasons"]), info["area_share"], info["primary"]
    else:                                                    # unknown crop: fall back to its tagged season
        eligible, shares, primary = [crop["season"]], {}, crop["season"]
    not_grown = {s: shares.get(s, 0.0) for s in ("kharif", "rabi", "zaid") if shares and s not in eligible}
    if season_request and season_request != "best" and crop["kind"] == "seasonal":
        if season_request not in eligible:
            return {"crop_id": crop["id"], "name": crop["name"], "name_hi": crop["name_hi"], "not_grown_in_season": season_request,
                    "observed_area_share": shares.get(season_request), "eligible_seasons": eligible}
        eligible_eval = [season_request]
    else:
        eligible_eval = eligible
    per = {s: score_crop_v2(crop_in, profile, irrigation, ev=ev, calibrator=cal, season=s) for s in eligible_eval}
    valid = {s: r for s, r in per.items() if r["score"] is not None}
    if not valid:
        return {**next(iter(per.values())), "seasonal": {}, "eligible_seasons": eligible}
    best = max(valid, key=lambda s: (valid[s]["calibrated_score"], valid[s]["_headroom"], s == primary))
    out = dict(valid[best])
    entries = {s: _season_entry(r, s, shares.get(s), ev, s == primary) for s, r in valid.items()}
    out["season"] = best
    out["best_season"] = best
    out["eligible_seasons"] = eligible
    out["seasonal"] = entries
    out["season_highlight"] = _highlight(crop["name"], entries, best, {} if (season_request and season_request != "best") else not_grown)
    out["seasons_not_grown"] = {s: round(v, 4) for s, v in not_grown.items()}
    out["season_evidence"] = {"source": SEASON_SOURCE, "area_share_by_season": shares} if shares else None
    return out
