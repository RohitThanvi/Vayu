"""
crop_suitability.py — "which crops can this location support?"

Pipeline (see also crop_requirements.py for the requirement table and its
provenance caveat):

  1. sample_location_profile(): sample soil (OpenLandMap 250m: pH, organic
     carbon, USDA texture class), slope (Copernicus DEM GLO-30) and a
     10-year monthly climate normal (CHIRPS rainfall, ERA5-Land temperature)
     for a point or an AOI. Global coverage — any lat/lon.
  2. score_crops(): score every crop in the requirement table against that
     profile, factor by factor (trapezoid membership: 1.0 inside the optimal
     range, tapering to 0 at the absolute limits), then take the WORST factor
     as the crop's score (FAO-style limiting-factor rule — one fatal problem
     is not averaged away by good soil). Reports the limiting factor and
     plain-language amendments.
  3. add_profit_estimates(): joins live Agmarknet mandi prices to a rough
     national-average yield for a REVENUE estimate. Deliberately not a
     profit figure: verified per-crop cost-of-cultivation data isn't in the
     system, so costs are left to the farmer rather than invented.

What this is NOT: a soil test. The soil layers are 250m modeled estimates
(OpenLandMap), not measurements. A farmer's own soil-test values can be passed
as `overrides` and replace the modeled ones — that is the single biggest
accuracy gain and the response labels which values came from where.

Scoring/profit functions below are pure Python (no GEE import) so they can be
unit-tested offline; only sample_location_profile() touches Earth Engine.
"""

import asyncio
import os
import logging
import math
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional

from .crop_requirements import CROPS, SEASON_MONTHS, TEXTURE_NAMES
from .evidence import _season_mean_temp, _season_rain, build_evidence, trapezoid  # noqa: F401  (re-exported)
from . import suitability_engine as _engine

logger = logging.getLogger(__name__)

_POINT_BUFFER_M = 300          # ~1 OpenLandMap pixel around a tapped point
_SOIL_SCALE_M = 250            # OpenLandMap native
_RAIN_SCALE_M = 5566           # CHIRPS native
_TEMP_SCALE_M = 11132          # ERA5-Land native
_DEM_SCALE_M = 30
_CLIMATE_YEARS = 10

_PH_ASSET = "OpenLandMap/SOL/SOL_PH-H2O_USDA-4C1A2A_M/v02"          # band values = pH x 10
_OC_ASSET = "OpenLandMap/SOL/SOL_ORGANIC-CARBON_USDA-6A1C_M/v02"    # band values = g/kg / 5 (i.e. x5 g/kg unit)
_TEX_ASSET = "OpenLandMap/SOL/SOL_TEXTURE-CLASS_USDA-TT_M/v02"      # class codes 1-12
_TEMP_ASSET = "ECMWF/ERA5_LAND/MONTHLY_AGGR"                         # temperature_2m, Kelvin
_RAIN_ASSET = "UCSB-CHG/CHIRPS/DAILY"                                # precipitation, mm/day

FACTOR_LABELS = {
    "temperature": "Temperature", "water": "Rainfall / water supply", "ph": "Soil pH",
    "texture": "Soil texture", "organic_carbon": "Organic carbon", "slope": "Slope",
}


# ═════════════════════════════════════════════════════════════════════════════
# 1. Sampling (the only GEE-touching part)
# ═════════════════════════════════════════════════════════════════════════════

# Identifies the code that produced a response, so validation runs can record what they actually tested (Render sets RENDER_GIT_COMMIT).
ENGINE_VERSION = (os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("VAYU_GIT_COMMIT") or "unknown")[:7]
ENGINE_FEATURES = ("seasonal_v1", "water_balance_v1")


def _aoi_latitude(aoi: Dict[str, Any]) -> Optional[float]:
    """Mid-latitude of an AOI's bounding box, from the GeoJSON itself (used only for the PET day-length correction)."""
    lats: List[float] = []

    def walk(o):
        if isinstance(o, dict):
            for k in ("geometry", "geometries", "features"):
                if k in o:
                    walk(o[k])
            if "coordinates" in o:
                walk(o["coordinates"])
        elif isinstance(o, (list, tuple)):
            if len(o) >= 2 and all(isinstance(v, (int, float)) for v in o[:2]):
                lats.append(float(o[1]))
            else:
                for v in o:
                    walk(v)
    try:
        walk(aoi)
    except Exception:
        return None
    return (min(lats) + max(lats)) / 2.0 if lats else None


def sample_location_profile(lat: Optional[float] = None, lon: Optional[float] = None,
                            aoi: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    import ee
    from ..gee_client import _polygon_geometry

    if aoi:
        region = _polygon_geometry(aoi)
        mode = "aoi"
    elif lat is not None and lon is not None:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("lat/lon out of range.")
        region = ee.Geometry.Point([lon, lat]).buffer(_POINT_BUFFER_M)
        mode = "point"
    else:
        raise ValueError("Provide either lat+lon or an AOI.")

    y1 = datetime.utcnow().year - 1
    y0 = y1 - _CLIMATE_YEARS + 1
    n_years = y1 - y0 + 1

    # OpenLandMap ships 6 depth bands (b0,b10,b30,b60,b100,b200). Topsoil
    # (0-30 cm) = trapezoid-weighted mean of the b0/b10/b30 points:
    # (5*b0 + 15*b10 + 10*b30) / 30  (piecewise-linear integral over 0-30cm).
    def _topsoil(img):
        b0, b10, b30 = img.select("b0"), img.select("b10"), img.select("b30")
        return b0.multiply(5).add(b10.multiply(15)).add(b30.multiply(10)).divide(30)

    ph_top = _topsoil(ee.Image(_PH_ASSET)).divide(10).rename("ph")
    oc_top = _topsoil(ee.Image(_OC_ASSET)).multiply(5).rename("oc_gkg")
    soil_img = ee.Image.cat([ph_top, oc_top])
    tex_img = ee.Image(_TEX_ASSET).select("b10").rename("texture")

    dem = ee.ImageCollection("COPERNICUS/DEM/GLO30_2024_1").select("DEM").mosaic()
    slope_img = ee.Terrain.slope(dem).rename("slope_deg")

    chirps = ee.ImageCollection(_RAIN_ASSET).filterDate(f"{y0}-01-01", f"{y1 + 1}-01-01").select("precipitation")
    era = ee.ImageCollection(_TEMP_ASSET).filterDate(f"{y0}-01-01", f"{y1 + 1}-01-01").select("temperature_2m")
    rain_img = ee.Image.cat([
        chirps.filter(ee.Filter.calendarRange(m, m, "month")).sum().divide(n_years).rename(f"r{m}")
        for m in range(1, 13)
    ])
    temp_img = ee.Image.cat([
        era.filter(ee.Filter.calendarRange(m, m, "month")).mean().subtract(273.15).rename(f"t{m}")
        for m in range(1, 13)
    ])

    def _reduce(img, scale, reducer):
        return img.reduceRegion(reducer=reducer, geometry=region, scale=scale,
                                maxPixels=1e9, bestEffort=True, tileScale=4).getInfo()

    jobs = {
        "soil": lambda: _reduce(soil_img, _SOIL_SCALE_M, ee.Reducer.mean()),
        "tex": lambda: _reduce(tex_img, _SOIL_SCALE_M, ee.Reducer.mode()),
        "slope": lambda: _reduce(slope_img, _DEM_SCALE_M, ee.Reducer.mean()),
        "rain": lambda: _reduce(rain_img, _RAIN_SCALE_M, ee.Reducer.mean()),
        "temp": lambda: _reduce(temp_img, _TEMP_SCALE_M, ee.Reducer.mean()),
    }
    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = {k: pool.submit(fn) for k, fn in jobs.items()}
        out = {k: f.result() for k, f in futs.items()}

    soil, tex, slope, rain, temp = out["soil"] or {}, out["tex"] or {}, out["slope"] or {}, out["rain"] or {}, out["temp"] or {}
    tex_code = tex.get("texture")
    tex_code = int(round(tex_code)) if tex_code is not None else None
    rain_m = [rain.get(f"r{m}") for m in range(1, 13)]
    temp_m = [temp.get(f"t{m}") for m in range(1, 13)]

    return {
        "mode": mode,
        "lat": lat if mode == "point" else _aoi_latitude(aoi),
        "ph": _r(soil.get("ph"), 2),
        "organic_carbon_gkg": _r(soil.get("oc_gkg"), 1),
        "texture_class": tex_code,
        "texture_name": TEXTURE_NAMES.get(tex_code),
        "slope_deg": _r(slope.get("slope_deg"), 1),
        "slope_pct": _r(math.tan(math.radians(slope["slope_deg"])) * 100, 1) if slope.get("slope_deg") is not None else None,
        "monthly_rain_mm": [_r(v, 1) for v in rain_m],
        "monthly_temp_c": [_r(v, 1) for v in temp_m],
        "annual_rain_mm": _r(sum(v for v in rain_m if v is not None), 0) if all(v is not None for v in rain_m) else None,
        "annual_mean_temp_c": _r(statistics.mean(v for v in temp_m if v is not None), 1) if all(v is not None for v in temp_m) else None,
        "climate_years": f"{y0}-{y1}",
        "sources": {
            "soil": "OpenLandMap (Hengl 2018), 250m modeled estimate, 0-30cm topsoil — CC-BY-SA-4.0",
            "rainfall": f"CHIRPS daily, {y0}-{y1} monthly means (~5.5km)",
            "temperature": f"ERA5-Land monthly, {y0}-{y1} (~11km), Copernicus Climate Change Service",
            "slope": "Copernicus DEM GLO-30 (30m)",
        },
    }


def _r(v, nd):
    return None if v is None else round(float(v), nd)


# ═════════════════════════════════════════════════════════════════════════════
# 2. Scoring (pure Python)
# ═════════════════════════════════════════════════════════════════════════════

def _rating(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.5:
        return "moderate"
    if score >= 0.25:
        return "low"
    return "unsuitable"


def _texture_name_to_code(name: Optional[str]) -> Optional[int]:
    if name is None:
        return None
    for code, n in TEXTURE_NAMES.items():
        if n.lower() == str(name).strip().lower():
            return code
    return None


def apply_overrides(profile: Dict[str, Any], overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Farmer soil-test values replace the modeled ones. Returns a copy with a
    `value_source` map so the UI can show which numbers are measured vs modeled."""
    p = dict(profile)
    src = {"ph": "modeled", "organic_carbon_gkg": "modeled", "texture": "modeled"}
    ov = overrides or {}
    if ov.get("ph") is not None:
        ph = float(ov["ph"])
        if not (3.0 <= ph <= 11.0):
            raise ValueError("pH override must be between 3 and 11.")
        p["ph"], src["ph"] = ph, "farmer soil test"
    if ov.get("organic_carbon_gkg") is not None:
        oc = float(ov["organic_carbon_gkg"])
        if not (0 <= oc <= 600):
            raise ValueError("Organic carbon override must be 0-600 g/kg.")
        p["organic_carbon_gkg"], src["organic_carbon_gkg"] = oc, "farmer soil test"
    if ov.get("texture"):
        code = _texture_name_to_code(ov["texture"])
        if code is None:
            raise ValueError(f"Unknown texture '{ov['texture']}'. Use one of: {', '.join(TEXTURE_NAMES.values())}.")
        p["texture_class"], p["texture_name"], src["texture"] = code, TEXTURE_NAMES[code], "farmer soil test"
    p["value_source"] = src
    return p


def score_crop(crop: Dict[str, Any], profile: Dict[str, Any], irrigation_available: bool) -> Dict[str, Any]:
    """Score ONE crop. Thin wrapper: the logic lives in suitability_engine (see docs/AGRI_SUITABILITY_ENGINE.md)."""
    return _engine.score_crop_v2(crop, profile, irrigation_available)


def score_crops(profile: Dict[str, Any], irrigation_available: bool = False,
                overrides: Optional[Dict[str, Any]] = None, season: Optional[str] = None) -> Dict[str, Any]:
    """season: None / 'best' (each crop in its best eligible season) or 'kharif' | 'rabi' | 'zaid' (only crops grown in that season)."""
    if season not in (None, "best", "kharif", "rabi", "zaid"):
        raise ValueError(f"season must be best, kharif, rabi or zaid (got {season!r})")
    p = apply_overrides(profile, overrides)
    ev = build_evidence(p)                                   # evidence is computed ONCE per location
    cal = _engine.get_calibrator()
    results = [_engine.score_crop_seasons(c, p, irrigation_available, ev=ev, calibrator=cal, season_request=season) for c in CROPS]
    not_grown = [{"crop_id": r["crop_id"], "name": r["name"], "name_hi": r["name_hi"], "observed_area_share": r["observed_area_share"]}
                 for r in results if r.get("not_grown_in_season")]
    results = [r for r in results if not r.get("not_grown_in_season")]
    ranked = _engine.rank_crops(results)                     # deterministic, tie-aware, on calibrated scores
    return {
        "profile": {**{k: p[k] for k in ("mode", "ph", "organic_carbon_gkg", "texture_class", "texture_name", "slope_pct",
                                         "annual_rain_mm", "annual_mean_temp_c", "climate_years", "value_source", "sources")},
                    "monthly_rain_mm": p.get("monthly_rain_mm"), "monthly_temp_c": p.get("monthly_temp_c"), "lat": p.get("lat")},
        "evidence": {k: ev[k] for k in ("dry_months", "longest_dry_run_months", "wet_season_share", "drainage", "missing")},
        "irrigation_available": irrigation_available,
        "engine_version": ENGINE_VERSION, "engine_features": list(ENGINE_FEATURES),
        "season_requested": season or "best",
        "season_windows": {k: v for k, v in SEASON_MONTHS.items() if k != "perennial"},
        "not_grown_in_season": not_grown,
        "seasonal_basis": ("Seasons a crop is evaluated in come from where it is actually grown (DES area by season, 2005-2014); "
                           "suitability within each season is scored on that season's temperature and rainfall window. "
                           "Sowing dates, varieties, photoperiod and vernalisation are not modelled."),
        "calibration_status": cal.status,
        "crops": ranked,
        "unscored": [r["crop_id"] for r in results if r["score"] is None],
        "requirements_source": "FAO EcoCrop (temperature, rainfall, pH, texture); other thresholds estimated",
        "disclaimer": (
            "Indicative suitability from modeled soil (250m) and climate data, not a soil test. "
            "Confirm with a soil test at your nearest KVK / soil-testing lab before investing. "
            "This scores whether land CAN physically support a crop, not whether it is commonly grown here -- "
            "local adoption also depends on irrigation infrastructure, market access, and tradition, which this does not model. "
            "Temperature, rainfall, pH and texture ranges come from the FAO EcoCrop database (generic species ranges, not tuned to local varieties, "
            "with any regional override noted per-crop where local agronomy data contradicts it); "
            "organic-carbon and slope thresholds and yields are rough estimates. "
            "Only an absolute temperature limit makes a crop 'unsuitable'; every other limitation lowers the score. "
            "Confidence reflects completeness of evidence, not a statistical interval."
        ),
    }


# ═════════════════════════════════════════════════════════════════════════════
# 3. Revenue estimate (mandi price × approximate national-average yield)
# ═════════════════════════════════════════════════════════════════════════════

def _to_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def price_summary(records: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """Modal price (Rs/quintal) summary across returned market records."""
    modal = [x for x in (_to_float(r.get("modal_price")) for r in records) if x and x > 0]
    if not modal:
        return None
    lows = [x for x in (_to_float(r.get("min_price")) for r in records) if x and x > 0]
    highs = [x for x in (_to_float(r.get("max_price")) for r in records) if x and x > 0]
    return {"modal": statistics.median(modal), "low": min(lows) if lows else min(modal),
            "high": max(highs) if highs else max(modal), "markets": len(modal)}


async def add_profit_estimates(result: Dict[str, Any], state: Optional[str] = None, top_n: int = 8) -> None:
    """Attach a revenue estimate to the top-N suitable crops, in place."""
    from . import mandi
    by_id = {c["id"]: c for c in CROPS}
    targets = [r for r in result["crops"] if r["rating"] in ("high", "moderate")][:top_n]

    # Agmarknet (data.gov.in) is slow and flaky even for one request at a time
    # (see mandi.py) -- firing up to top_n of these at once at the same key was
    # making the whole crop-suitability call time out even when each individual
    # lookup would have succeeded alone. Cap concurrency so requests queue
    # instead of piling on together; mandi.py's own cache means only the first
    # AOI/commodity combo actually pays this cost.
    sem = asyncio.Semaphore(2)

    async def one(r):
      async with sem:
        c = by_id[r["crop_id"]]
        if not c["mandi_commodity"] or not c["yield_q_per_ha"]:
            r["revenue"] = {"status": "no_price_data", "note": "No mandi price or typical-yield data for this crop."}
            return
        try:
            data = await mandi.get_mandi_prices(commodity=c["mandi_commodity"], state=state, limit=20)
        except Exception as e:  # network/parse — never break the suitability result
            logger.warning(f"profit: mandi fetch failed for {c['name']}: {e}")
            data = {"records": [], "error": str(e)}
        # Historical trend (avg price, CAGR) from CEDA, alongside the live data.gov.in
        # price above. Optional and best-effort — CEDA lags data.gov.in by design (see
        # ceda_prices.py), so its as_of_date is surfaced plainly rather than implying
        # this is today's price. Never blocks the primary price if CEDA is unconfigured
        # or fails.
        from . import ceda_prices
        trend = await ceda_prices.get_price_trend(commodity=c["mandi_commodity"], state=state)
        if trend.get("available"):
            r["price_trend"] = {
                "avg_price_rs_per_q": trend["avg_price_modal"], "cagr_pct": trend["cagr_pct"],
                "as_of": trend["as_of_date"], "period": trend["period"], "source": trend["source"],
            }
        ps = price_summary(data.get("records", []))
        if not ps:
            r["revenue"] = {"status": "price_unavailable", "note": data.get("error") or "No recent mandi records found."}
            return
        y = c["yield_q_per_ha"]
        r["revenue"] = {
            "status": "ok", "yield_q_per_ha": y, "price_rs_per_q_modal": round(ps["modal"]),
            "revenue_rs_per_ha": {"low": round(y * ps["low"]), "modal": round(y * ps["modal"]), "high": round(y * ps["high"])},
            "markets_used": ps["markets"],
            "note": "Gross revenue = approximate national-average yield × recent mandi price. Cost of cultivation NOT deducted; "
                    "actual yield varies by state, variety and practice.",
        }

    await asyncio.gather(*(one(r) for r in targets))
    priced = [r for r in targets if r.get("revenue", {}).get("status") == "ok"]
    for i, r in enumerate(sorted(priced, key=lambda r: -r["revenue"]["revenue_rs_per_ha"]["modal"]), start=1):
        r["revenue"]["revenue_rank_among_suitable"] = i
