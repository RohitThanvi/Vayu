"""
location_intel.py — free-data Location Intelligence for real estate (the Business tab's "Real Estate" view).

Scores any location (or compares up to 4) on three questions a developer, lender or buyer asks:

    Liveability & access   how close are hospitals, schools, markets, transit, parks, major roads?   (OpenStreetMap)
    Growth momentum        is economic activity around here rising?    (VIIRS night-light level + 4-year change)
    Hazard resilience      flood-prone water history, low-lying ground, slope, pre-monsoon heat, M5+ quakes
                           (JRC Global Surface Water, Copernicus DEM, Landsat, USGS)

Everything is free and keyless apart from Vayu's own Earth Engine access. Nothing here estimates a PRICE:
there is no free, open, record-level price source in India, and the tab says so.

Same auditable method as the siting agent: every score is a weighted average of per-criterion utilities
(1 = favourable, 0 = unfavourable) computed from MEASURED values; criteria with no data drop out and the
weights renormalise; below 50% evidence coverage a group is not scored; OSM "none found" is only treated as
signal where OSM demonstrably has coverage. Thresholds are screening heuristics, exposed in the output.
"""

import asyncio
import datetime as dt
import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from . import geocode_util, osm_amenities

try:                                    # shared Open-Meteo allowance ledger; optional so this module stands alone
    from .weather import openmeteo_budget as ob
except ImportError:                     # pragma: no cover
    ob = None

logger = logging.getLogger(__name__)

MAX_LOCATIONS = 4
MIN_RADIUS_KM, MAX_RADIUS_KM, DEFAULT_RADIUS_KM = 1.0, 4.0, 2.0
RING_RADIUS_KM = 8.0                  # "surroundings" for the relative-elevation check
GEE_CONCURRENCY = 3
GEE_TOOL_TIMEOUT_S = 90
MIN_COVERAGE_TO_SCORE = 0.5
NOMINATIM_GAP_S = 1.05
USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
QUAKE_RADIUS_KM = 200
QUAKE_START = "1996-01-01"

_KIND_METRIC = {"hospital": "hospital_km", "school": "school_km", "market": "market_km", "park": "park_km",
                "road": "road_km", "rail": "transit_km", "bus": "transit_km"}

METRICS: Dict[str, Tuple[str, str, str]] = {
    "hospital_km": ("Nearest hospital / clinic", "km", "OpenStreetMap"),
    "school_km": ("Nearest school / college", "km", "OpenStreetMap"),
    "market_km": ("Nearest supermarket / market", "km", "OpenStreetMap"),
    "transit_km": ("Nearest rail/metro station or bus stop", "km", "OpenStreetMap"),
    "park_km": ("Nearest park", "km", "OpenStreetMap"),
    "road_km": ("Nearest major road (segment centre)", "km", "OpenStreetMap"),
    "night_light_level": ("Night-light activity, last 12 months", "nW/sr/cm²", "VIIRS DNB monthly"),
    "night_light_change": ("Night-light change over ~4 years", "nW/sr/cm²", "VIIRS DNB monthly"),
    "night_light_growth_pct": ("Night-light growth over ~4 years", "%", "VIIRS DNB monthly"),
    "seasonal_water_pct": ("Seasonal / flood-prone water coverage", "%", "JRC Global Surface Water"),
    "relative_elev_m": ("Elevation vs 8 km surroundings", "m", "Copernicus DEM GLO-30"),
    "slope_deg": ("Mean slope", "°", "Copernicus DEM GLO-30"),
    "lst_c": ("Pre-monsoon land-surface temperature", "°C", "Landsat 8/9 Collection 2"),
    "quake_m5_count": (f"M5+ earthquakes within {QUAKE_RADIUS_KM} km since 1996", "count", "USGS ComCat"),
}

# metric -> (good_at, bad_at, weight); weights inside a group sum to 1. Direction is implied by the order.
GROUPS: Dict[str, Dict[str, Any]] = {
    "liveability": {"label": "Liveability & access", "criteria": {
        "hospital_km": (2, 10, 0.22), "school_km": (1, 6, 0.20), "market_km": (1.5, 8, 0.18),
        "transit_km": (1, 6, 0.22), "park_km": (1, 5, 0.08), "road_km": (1, 8, 0.10)}},
    "growth": {"label": "Growth momentum", "criteria": {
        "night_light_level": (10, 1, 0.30), "night_light_change": (2.0, -0.3, 0.40), "night_light_growth_pct": (30, 0, 0.30)}},
    "resilience": {"label": "Hazard resilience", "criteria": {
        "seasonal_water_pct": (2, 20, 0.28), "relative_elev_m": (0, -15, 0.20), "slope_deg": (8, 25, 0.10),
        "lst_c": (36, 46, 0.17), "quake_m5_count": (0, 10, 0.25)}},
}
PROFILES: Dict[str, Dict[str, Any]] = {
    "balanced": {"label": "Balanced", "weights": {"liveability": 0.4, "growth": 0.3, "resilience": 0.3}},
    "family_buyer": {"label": "Family buyer", "weights": {"liveability": 0.5, "growth": 0.2, "resilience": 0.3}},
    "investor": {"label": "Growth investor", "weights": {"liveability": 0.2, "growth": 0.5, "resilience": 0.3}},
    "lender_risk": {"label": "Lender / insurer (risk-first)", "weights": {"liveability": 0.2, "growth": 0.2, "resilience": 0.6}},
}
SOURCES = [
    "© OpenStreetMap contributors (ODbL) — amenities and roads",
    "VIIRS Day/Night Band monthly composites (Earth Observation Group) — night-light activity",
    "JRC Global Surface Water (European Commission) — water history",
    "Copernicus DEM GLO-30 (ESA) — terrain",
    "Landsat 8/9 Collection 2 Level-2 (USGS) — surface temperature",
    "ESA WorldCover v200 (CC BY 4.0) — land cover",
    "USGS Earthquake Catalog (ComCat) — seismicity",
    "Open-Meteo Air Quality API — current air quality (context only; free tier is non-commercial)",
]
METHODOLOGY = (
    "Each group score (0-100) is the weighted average of per-criterion utilities (1 = favourable, 0 = unfavourable) "
    "computed from measured values; criteria with no data are excluded and weights renormalised, and a group needs "
    "≥50% evidence coverage to be scored. The overall score weights the three groups by the chosen profile. Thresholds "
    "and weights are editable screening heuristics, not standards. Values describe a 1-4 km circle around the location, "
    "not a specific plot. This tool does NOT estimate prices or returns: no free open record-level price data exists for "
    "India, and nothing here is investment, valuation or legal advice. Any price shown is exactly what you entered."
)


# ── scoring (pure) ────────────────────────────────────────────────────────────
def utility(value: float, good_at: float, bad_at: float) -> float:
    if good_at == bad_at:
        return 1.0
    return max(0.0, min(1.0, (value - bad_at) / (good_at - bad_at)))


def score_group(group: str, metrics: Dict[str, Optional[float]], notes: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    spec = GROUPS[group]["criteria"]
    notes = notes or {}
    criteria, total_w, avail_w, acc = [], 0.0, 0.0, 0.0
    for metric, (good, bad, w) in spec.items():
        label, unit, source = METRICS[metric]
        v = metrics.get(metric)
        u = None if v is None else utility(v, good, bad)
        total_w += w
        if u is not None:
            avail_w += w
            acc += w * u
        criteria.append({"metric": metric, "label": label, "unit": unit, "source": source,
                         "value": None if v is None else round(v, 3), "utility": None if u is None else round(u, 3),
                         "weight": w, "good_at": good, "bad_at": bad, "note": notes.get(metric, "")})
    coverage = avail_w / total_w if total_w else 0.0
    score = round(100.0 * acc / avail_w, 1) if (avail_w and coverage >= MIN_COVERAGE_TO_SCORE) else None
    level = "strong" if coverage >= 0.8 else "partial" if coverage >= MIN_COVERAGE_TO_SCORE else "weak"
    return {"label": GROUPS[group]["label"], "score": score, "coverage": round(coverage, 2), "evidence_level": level, "criteria": criteria}


def overall(groups: Dict[str, Dict[str, Any]], weights: Dict[str, float]) -> Tuple[Optional[float], float]:
    avail = {g: w for g, w in weights.items() if groups[g]["score"] is not None}
    total = sum(weights.values())
    cov = sum(avail.values()) / total if total else 0.0
    if not avail or cov < MIN_COVERAGE_TO_SCORE:
        return None, round(cov, 2)
    return round(sum(groups[g]["score"] * w for g, w in avail.items()) / sum(avail.values()), 1), round(cov, 2)


def rank(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    scored = sorted([i for i in items if i["overall_score"] is not None], key=lambda i: -i["overall_score"])
    for n, it in enumerate(scored, 1):
        it["rank"] = n
    rest = [i for i in items if i["overall_score"] is None]
    for it in rest:
        it["rank"] = None
    return scored + rest


def highlights(groups: Dict[str, Dict[str, Any]], k: int = 2) -> Dict[str, List[str]]:
    rows = []
    for g in groups.values():
        for c in g["criteria"]:
            if c["utility"] is not None:
                rows.append((c["weight"], c))
    fmt = lambda c: f"{c['label'].split(' (')[0].lower()} {c['value']:g} {c['unit']}"
    strengths = [fmt(c) for w, c in sorted(rows, key=lambda r: -r[0] * r[1]["utility"]) if c["utility"] >= 0.8][:k]
    weaknesses = [fmt(c) for w, c in sorted(rows, key=lambda r: -r[0] * (1 - r[1]["utility"])) if c["utility"] <= 0.35][:k]
    return {"strengths": strengths, "weaknesses": weaknesses}


def make_flags(metrics: Dict[str, Any], context: Dict[str, Any], osm_sparse: bool) -> List[str]:
    f = []
    if (metrics.get("seasonal_water_pct") or 0) >= 15:
        f.append(f"Seasonal/flood-prone water covers {metrics['seasonal_water_pct']:.0f}% of this area.")
    if (metrics.get("relative_elev_m") if metrics.get("relative_elev_m") is not None else 0) <= -10:
        f.append(f"Sits about {abs(metrics['relative_elev_m']):.0f} m below its surroundings (drainage / ponding risk).")
    if (metrics.get("quake_m5_count") or 0) >= 5:
        f.append(f"{int(metrics['quake_m5_count'])} M5+ earthquakes recorded within {QUAKE_RADIUS_KM} km since 1996.")
    if (metrics.get("lst_c") or 0) >= 44:
        f.append(f"Very hot surface in the pre-monsoon window ({metrics['lst_c']:.0f} °C).")
    if metrics.get("night_light_growth_pct") is not None and metrics["night_light_growth_pct"] < 0 and (metrics.get("night_light_level") or 0) >= 3:
        f.append("Night-light activity has declined over the period.")
    aqi = (context.get("air_quality") or {}).get("us_aqi")
    if aqi is not None and aqi >= 150:
        f.append(f"Air quality is currently unhealthy (US AQI {aqi:.0f}).")
    if osm_sparse:
        f.append("OpenStreetMap looks sparsely mapped here, so accessibility criteria were not scored.")
    return f


# ── pure data helpers ─────────────────────────────────────────────────────────
def night_light_stats(points: List[Dict[str, Any]]) -> Dict[str, float]:
    vals = [p["value"] for p in sorted(points or [], key=lambda p: p.get("date", "")) if p.get("value") is not None]
    out: Dict[str, float] = {}
    if len(vals) >= 6:
        out["night_light_level"] = round(sum(vals[-12:]) / len(vals[-12:]), 3)
    if len(vals) >= 24:
        first, last = sum(vals[:12]) / 12.0, sum(vals[-12:]) / 12.0
        out["night_light_change"] = round(last - first, 3)
        if first >= 1.0:
            out["night_light_growth_pct"] = round((last - first) / first * 100.0, 1)
    return out


def parse_usgs(geojson: Dict[str, Any], lat: float, lon: float) -> Dict[str, Any]:
    feats = (geojson or {}).get("features") or []
    mags, m5_dist = [], []
    for f in feats:
        mag = (f.get("properties") or {}).get("mag")
        coords = (f.get("geometry") or {}).get("coordinates") or []
        if mag is None:
            continue
        mags.append(float(mag))
        if float(mag) >= 5.0 and len(coords) >= 2:
            m5_dist.append(osm_amenities.haversine_km(lat, lon, coords[1], coords[0]))
    return {"quake_m5_count": float(sum(1 for m in mags if m >= 5.0)), "quake_max_mag": max(mags) if mags else None,
            "quake_m45_count": len(mags), "quake_nearest_m5_km": round(min(m5_dist), 0) if m5_dist else None}


def lst_window(today: Optional[dt.date] = None) -> Tuple[str, str]:
    """Most recent completed pre-monsoon window (1 Mar - 30 Jun) — clear skies, peak heat stress in India."""
    t = today or dt.date.today()
    year = t.year if t.month >= 7 else t.year - 1
    return f"{year}-03-01", f"{year}-06-30"


# ── data sources ──────────────────────────────────────────────────────────────
def _rs():
    from . import gee_remote_sensing as rs     # lazy: keeps earthengine out of import time and tests
    return rs


async def _gee(tool: str, aoi: dict, extra: dict, fn: Callable[[], Any], sem: asyncio.Semaphore) -> Any:
    """Run a blocking GEE computation in a thread, through the shared result cache, under a timeout."""
    from . import gee_cache

    def run():
        return gee_cache.cached_compute(f"re_{tool}", {"aoi": aoi, **extra}, fn)
    async with sem:
        return await asyncio.wait_for(asyncio.to_thread(run), GEE_TOOL_TIMEOUT_S)


async def _src_terrain(aoi, ring, sem) -> Dict[str, float]:
    rs = _rs()
    here = await _gee("terrain", aoi, {}, lambda: rs.compute_terrain_analysis(aoi), sem)
    out = {"slope_deg": float(here["slope_degrees"]["mean"]), "elevation_m": float(here["elevation_m"]["mean"])}
    try:
        around = await _gee("terrain_ring", ring, {}, lambda: rs.compute_terrain_analysis(ring), sem)
        out["relative_elev_m"] = round(out["elevation_m"] - float(around["elevation_m"]["mean"]), 1)
    except Exception as e:
        logger.info(f"surroundings terrain unavailable: {type(e).__name__}")
    return out


async def _src_landcover(aoi, sem) -> Dict[str, float]:
    rs = _rs()
    r = await _gee("lulc", aoi, {}, lambda: rs.compute_lulc_classification(aoi), sem)
    by = {c["code"]: c["pct_of_aoi"] for c in r.get("classes", [])}
    return {"landcover_builtup_pct": by.get(50, 0.0), "landcover_cropland_pct": by.get(40, 0.0),
            "landcover_tree_pct": by.get(10, 0.0), "landcover_water_pct": by.get(80, 0.0) + by.get(90, 0.0)}


async def _src_night(aoi, sem) -> Dict[str, float]:
    rs = _rs()
    end = dt.date.today() - dt.timedelta(days=100)               # VIIRS product lags real time
    start = end - dt.timedelta(days=4 * 365 + 30)
    r = await _gee("night", aoi, {"start": start.isoformat(), "end": end.isoformat()},
                   lambda: rs.compute_night_lights(aoi, start.isoformat(), end.isoformat()), sem)
    stats = night_light_stats(r.get("points") or [])
    if not stats:
        raise ValueError("too few usable night-light months")
    return stats


async def _src_lst(aoi, sem) -> Dict[str, float]:
    rs = _rs()
    s, e = lst_window()
    r = await _gee("lst", aoi, {"start": s, "end": e}, lambda: rs.compute_land_surface_temperature(aoi, s, e), sem)
    lst = r.get("lst_celsius")
    if not lst or lst.get("mean") is None:
        raise ValueError("no cloud-free Landsat scene in the pre-monsoon window")
    return {"lst_c": float(lst["mean"])}


def _water_history(aoi: dict) -> Dict[str, float]:
    rs = _rs()
    ee = rs.ee
    region = rs._polygon_geometry(aoi)
    gsw = ee.Image("JRC/GSW1_4/GlobalSurfaceWater")
    # .unmask(0): these bands are MASKED wherever water was never observed — without it the means below
    # would silently ignore every dry pixel (the same trap that broke the flood tool).
    ever = gsw.select("max_extent").unmask(0).eq(1)
    permanent = gsw.select("occurrence").unmask(0).gte(90)
    seasonal = ever.And(permanent.Not())
    stack = ee.Image.cat([seasonal.rename("seasonal"), permanent.rename("permanent")]).multiply(100)
    stats = stack.reduceRegion(reducer=ee.Reducer.mean(), geometry=region, scale=30, maxPixels=1e9,
                               bestEffort=True, tileScale=2).getInfo()
    if stats.get("seasonal") is None:
        raise ValueError("no surface-water statistics for this area")
    return {"seasonal_water_pct": round(float(stats["seasonal"]), 2), "permanent_water_pct": round(float(stats.get("permanent") or 0.0), 2)}


async def _src_water(aoi, sem) -> Dict[str, float]:
    return await _gee("jrc_water", aoi, {}, lambda: _water_history(aoi), sem)


async def _src_quakes(lat: float, lon: float) -> Dict[str, Any]:
    params = {"format": "geojson", "latitude": round(lat, 4), "longitude": round(lon, 4), "maxradiuskm": QUAKE_RADIUS_KM,
              "starttime": QUAKE_START, "minmagnitude": 4.5, "limit": 20000}
    last = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                resp = await client.get(USGS_URL, params=params)
            resp.raise_for_status()
            return parse_usgs(resp.json(), lat, lon)
        except Exception as e:
            last = e
            await asyncio.sleep(0.8)
    raise RuntimeError(f"USGS earthquake catalog unavailable ({type(last).__name__})")


async def _src_air(lat: float, lon: float) -> Dict[str, Any]:
    from .weather import air_quality
    if ob is not None:
        ob.BUDGET.record(1)
    r = await air_quality.get_air_quality(lat, lon)
    if r.get("error") or r.get("us_aqi") is None:
        raise RuntimeError("air-quality service unavailable")
    return {"us_aqi": r["us_aqi"], "category": r.get("category"), "pm2_5": r.get("pm2_5"), "observed_at": r.get("observed_at")}


# ── prices (user-supplied only) ──────────────────────────────────────────────
PRICE_SOURCE = "Entered by you — Vayu does not fetch or verify prices"
PRICE_RESOURCES = [
    {"label": "India.gov.in — Find land guideline values (links to each state's portal)", "url": "https://www.india.gov.in/find-land-guideline-values-0",
     "note": "Circle rate / ready reckoner / guideline value: the government's MINIMUM registration value, usually below market price."},
    {"label": "Karnataka — revised guidelines value", "url": "https://igr.karnataka.gov.in/page/Revised+Guidelines+Value/en", "note": "Official state page."},
    {"label": "Tamil Nadu — TNREGINET (guideline value search)", "url": "https://tnreginet.gov.in/portal/?UserLocaleID=en", "note": "Official state portal."},
    {"label": "RBI Database on Indian Economy — House Price Index (18 cities)", "url": "https://data.rbi.org.in/DBIE/#/dbie/home",
     "note": "Built from registration transaction data. An INDEX (base 2022-23 = 100) for trend, not a price per sq ft. Path: Statistics > Real Sector > Prices & Wages."},
]


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 and math.isfinite(f) else None


def price_block(price: Optional[float], circle: Optional[float]) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """What the user typed, plus the one comparison that needs no outside data: market vs circle rate."""
    if price is None and circle is None:
        return None, []
    blk: Dict[str, Any] = {"price_per_sqft": price, "circle_rate_per_sqft": circle, "source": PRICE_SOURCE}
    flags: List[str] = []
    if price is not None and circle is not None:
        blk["premium_to_circle_pct"] = round((price / circle - 1.0) * 100.0, 1)
        if price < circle:
            flags.append("The price you entered is below the circle rate you entered. Registration duty is generally charged on the higher "
                         "of the two, so double-check both numbers.")
    return blk, flags


def add_value_ranks(items: List[Dict[str, Any]]) -> bool:
    """Score per ₹1,000/sq ft, ranked — only when at least two compared locations have both a price and an overall score.
    A comparison aid for the places the user entered, NOT a valuation."""
    rows = [i for i in items if i.get("price") and i["price"].get("price_per_sqft") and i["overall_score"] is not None]
    if len(rows) < 2:
        return False
    for i in rows:
        i["price"]["score_per_1000"] = round(i["overall_score"] / (i["price"]["price_per_sqft"] / 1000.0), 2)
    for n, i in enumerate(sorted(rows, key=lambda r: -r["price"]["score_per_1000"]), 1):
        i["price"]["value_rank"], i["price"]["value_rank_of"] = n, len(rows)
    return True


# ── locations ─────────────────────────────────────────────────────────────────
def _parse_latlon(text: str) -> Optional[Tuple[float, float]]:
    parts = [p for p in text.replace(";", ",").split(",")]
    if len(parts) == 2:
        try:
            lat, lon = float(parts[0]), float(parts[1])
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
        except ValueError:
            pass
    return None


def _center(geom: dict) -> Optional[Tuple[float, float]]:
    pts: List[Tuple[float, float]] = []

    def walk(c):
        if isinstance(c, (list, tuple)) and len(c) >= 2 and all(isinstance(v, (int, float)) for v in c[:2]):
            pts.append((float(c[1]), float(c[0])))
        elif isinstance(c, (list, tuple)):
            for x in c:
                walk(x)
    walk((geom or {}).get("coordinates"))
    if not pts:
        return None
    lats, lons = [p[0] for p in pts], [p[1] for p in pts]
    return (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2


async def resolve_locations(inputs: List[dict], radius_km: float, caveats: List[str],
                            diag: Optional[dict] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    radius = max(MIN_RADIUS_KM, min(float(radius_km or DEFAULT_RADIUS_KM), MAX_RADIUS_KM))
    for idx, item in enumerate((inputs or [])[:MAX_LOCATIONS]):
        name = (item.get("name") or item.get("query") or "").strip()
        lat, lon = item.get("lat"), item.get("lon")
        if lat is None or lon is None:
            ll = _parse_latlon(item.get("query") or "")
            if ll:
                lat, lon = ll
        if lat is None or lon is None:
            q = (item.get("query") or name).strip()
            if not q:
                continue
            if out or idx:
                await asyncio.sleep(NOMINATIM_GAP_S)             # Nominatim usage policy: max ~1 request/second
            gdiag: dict = {}
            try:
                c = await geocode_util.geocode_point(q, gdiag)
            except Exception as e:
                c, gdiag = None, {"geocode_error": f"lookup failed ({type(e).__name__})"}
            if not c:
                if gdiag.get("geocode_error"):
                    if diag is not None:
                        diag["geocode_error"] = gdiag["geocode_error"]
                    caveats.append(f"'{q}' was skipped because {gdiag['geocode_error']}. Try again shortly, or enter it as 'lat, lon'.")
                else:
                    caveats.append(f"Could not find '{q}' on the map, so it was skipped.")
                continue
            lat, lon = c
            name = name or q
        out.append({"name": name or f"{lat:.4f}, {lon:.4f}", "lat": float(lat), "lon": float(lon), "radius_km": radius,
                    "price_per_sqft": _num(item.get("price_per_sqft")), "circle_rate_per_sqft": _num(item.get("circle_rate_per_sqft")),
                    "aoi": geocode_util._point_to_circle_polygon(float(lat), float(lon), radius),
                    "ring": geocode_util._point_to_circle_polygon(float(lat), float(lon), RING_RADIUS_KM)})
    if len(inputs or []) > MAX_LOCATIONS:
        caveats.append(f"Only the first {MAX_LOCATIONS} locations were analysed.")
    return out


# ── orchestration ─────────────────────────────────────────────────────────────
def _progress(cb, label: str, pct: int) -> None:
    if cb:
        try:
            cb(label, pct)
        except Exception:
            pass


async def assess_locations(inputs: List[dict], radius_km: float = DEFAULT_RADIUS_KM, profile: str = "balanced",
                           progress: Optional[Callable[[str, int], None]] = None) -> Dict[str, Any]:
    prof = PROFILES.get(profile) or PROFILES["balanced"]
    profile = profile if profile in PROFILES else "balanced"
    caveats: List[str] = []
    diag: Dict[str, Any] = {}

    _progress(progress, "Locating places", 5)
    locs = await resolve_locations(inputs, radius_km, caveats, diag)
    if not locs:
        if diag.get("geocode_error"):
            raise ValueError("The place-name lookup service is busy right now, so the location could not be looked up. "
                             "Wait a minute and try again, or enter it as 'lat, lon' to skip the lookup.")
        raise ValueError("None of the locations could be found. Try a place name with its city, or 'lat, lon'.")

    sem = asyncio.Semaphore(GEE_CONCURRENCY)
    state = [{"loc": l, "metrics": {}, "notes": {}, "context": {}, "errors": []} for l in locs]
    jobs: List[Tuple[int, str, Callable[[], Any]]] = []
    for i, l in enumerate(locs):
        jobs += [(i, "terrain", lambda l=l: _src_terrain(l["aoi"], l["ring"], sem)),
                 (i, "land cover", lambda l=l: _src_landcover(l["aoi"], sem)),
                 (i, "night lights", lambda l=l: _src_night(l["aoi"], sem)),
                 (i, "flood history", lambda l=l: _src_water(l["aoi"], sem)),
                 (i, "surface temperature", lambda l=l: _src_lst(l["aoi"], sem)),
                 (i, "earthquakes", lambda l=l: _src_quakes(l["lat"], l["lon"])),
                 (i, "air quality", lambda l=l: _src_air(l["lat"], l["lon"]))]
    done = {"n": 0}
    total = len(jobs) + 1

    async def run(i: int, name: str, factory) -> None:
        try:
            res = await factory()
            if name == "air quality":
                state[i]["context"]["air_quality"] = res
            elif name == "earthquakes":
                state[i]["context"]["seismic"] = {k: res[k] for k in ("quake_max_mag", "quake_m45_count", "quake_nearest_m5_km")}
                state[i]["metrics"]["quake_m5_count"] = res["quake_m5_count"]
            else:
                state[i]["metrics"].update({k: v for k, v in res.items() if v is not None})
        except Exception as e:
            state[i]["errors"].append(f"{name}: {type(e).__name__}: {str(e)[:110]}")
            logger.warning(f"Location intel: {name} failed for {locs[i]['name']}: {e}")
        done["n"] += 1
        _progress(progress, f"Measuring ({done['n']}/{total})", 10 + int(80 * done["n"] / total))

    centers = [(l["lat"], l["lon"]) for l in locs]
    _progress(progress, "Measuring terrain, land use, activity, hazards and nearby amenities", 10)
    osm_task = asyncio.create_task(osm_amenities.fetch(centers, diag))
    await asyncio.gather(*[run(i, n, f) for i, n, f in jobs])
    osm = await osm_task
    done["n"] += 1

    osm_missing = osm is None
    items: List[Dict[str, Any]] = []
    for si, st in enumerate(state):
        l = st["loc"]
        sparse = False
        amenity: Dict[str, Any] = {}
        if osm is not None:
            summ = osm_amenities.summarize((l["lat"], l["lon"]), osm)
            st["metrics"].update(summ["metrics"])
            st["notes"].update(summ["notes"])
            sparse = summ["sparse"]
            # A later search ring failed: "none mapped" for those kinds was never actually checked, so drop the
            # criterion rather than score an unchecked amenity as absent.
            for kind in (diag.get("osm_unverified") or {}).get(si, []):
                m = _KIND_METRIC.get(kind)
                if m and m in summ["notes"]:
                    st["metrics"].pop(m, None)
                    st["notes"].pop(m, None)
            amenity = {"nearby": summ["nearby"], "counts": summ["counts"], "count_radius_km": summ["count_radius_km"],
                       "search_radius_km": summ["search_radius_km"]}
        osm_status = "unavailable" if osm is None else "sparse" if sparse else "ok"
        groups = {g: score_group(g, st["metrics"], st["notes"]) for g in GROUPS}
        ov, cov = overall(groups, prof["weights"])
        ctx = {**st["context"], "amenities": amenity, "osm_status": osm_status, "amenity_counts": amenity.get("counts", {}),
               "terrain": {"elevation_m": st["metrics"].get("elevation_m"), "slope_deg": st["metrics"].get("slope_deg")},
               "land_cover": {k.replace("landcover_", "").replace("_pct", ""): round(v, 1) for k, v in st["metrics"].items() if k.startswith("landcover_")},
               "permanent_water_pct": st["metrics"].get("permanent_water_pct")}
        for e in st["errors"]:
            caveats.append(f"{l['name']} — {e}")
        pblk, pflags = price_block(l.get("price_per_sqft"), l.get("circle_rate_per_sqft"))
        items.append({"name": l["name"], "lat": round(l["lat"], 5), "lon": round(l["lon"], 5), "radius_km": l["radius_km"],
                      "overall_score": ov, "overall_coverage": cov, "groups": groups, "context": ctx, "price": pblk,
                      "flags": make_flags(st["metrics"], st["context"], sparse) + pflags, **highlights(groups), "errors": st["errors"]})
    if osm is not None and diag.get("osm_partial"):
        caveats.append(f"Part of the amenity search did not finish because {diag['osm_partial']}; amenities shown as 'none mapped' "
                       "beyond the nearest rings may simply not have been checked, so treat those distances as lower bounds.")
    if osm_missing:
        caveats.append(f"Amenity data was not available because {diag.get('osm_error', 'OpenStreetMap could not be read')}, so liveability criteria were not scored.")
    value_ranked = add_value_ranks(items)
    if sum(1 for i in items if i.get("price") and i["price"].get("price_per_sqft")) >= 2 and not value_ranked:
        caveats.append("Value comparison needs at least two priced locations that could also be scored overall.")
    ranked = rank(items)
    for it in ranked:
        if it["overall_score"] is None:
            caveats.append(f"{it['name']} had too little measurable evidence to be scored overall.")
    _progress(progress, "Done", 100)
    return {"profile": profile, "profile_label": prof["label"], "weights": prof["weights"], "radius_km": locs[0]["radius_km"],
            "locations": ranked, "caveats": caveats, "methodology": METHODOLOGY, "sources": SOURCES,
            "generated_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "price_resources": PRICE_RESOURCES,
            "not_included": ["Market prices, rents and returns are not fetched (no free open per-area price data exists for India); only prices you type in are shown",
                             "RERA project / supply-pipeline data (state portals differ; not yet connected)"]}
