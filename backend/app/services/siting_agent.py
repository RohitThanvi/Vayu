"""
siting_agent.py — the "combined agent": turns web-research candidates into an
evidence-backed, ranked shortlist by measuring each one with Vayu's OWN data.

    web research (research_agent)        ->  candidate localities + context
    geocode each candidate               ->  a small circular AOI per candidate
    Earth Engine (terrain, land cover,   ->  slope, cropland/built-up/tree/water
        night lights)                        share, lit-activity (demand proxy)
    OpenStreetMap / Overpass             ->  distance to substations & major roads,
                                             mapped telecom towers / EV chargers
    deterministic screening score        ->  0-100, per-criterion, fully auditable
    LLM                                  ->  explains the ranking; does NOT compute it

Design rules (this is what makes it defensible rather than a chatbot):

  * The ranking is computed in code from measured values, never by the LLM. Every
    score carries its per-criterion breakdown, weight, source and data coverage.
  * Missing evidence is never silently treated as good or bad: a criterion with no
    data drops out of that candidate's score (weights renormalised), and coverage is
    reported. Below 50% coverage a candidate is not scored at all.
  * OpenStreetMap is incomplete in much of India. "Nothing mapped" is only treated as
    meaningful when OSM demonstrably has coverage in the area (see _apply_osm_guards).
  * The utility thresholds below are SCREENING HEURISTICS (editable defaults), not
    regulatory standards. They are exposed in the output so a reader can disagree.
  * Web claims are cross-checked against measurements; conflicts are surfaced.

Satellite/OSM numbers describe the ~1-4 km circle around the geocoded locality
centre, not the specific plot a developer would buy. This is pre-feasibility
screening, and the output says so.
"""

import asyncio
import datetime
import logging
import math
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

import httpx

from . import geocode_util

logger = logging.getLogger(__name__)

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
_HEADERS = {"User-Agent": "VAYU-Siting-Agent/1.0"}

MAX_CANDIDATES = 4
GEE_CONCURRENCY = 3
GEE_TOOL_TIMEOUT_S = 70
OSM_TIMEOUT_S = 35
MIN_COVERAGE_TO_SCORE = 0.5
MAX_CANDIDATE_DISTANCE_FROM_REGION_KM = 150     # guards against ambiguous place names geocoding to the wrong city
NOMINATIM_GAP_S = 1.05                          # Nominatim usage policy: max ~1 request/second


# ── metric catalogue ──────────────────────────────────────────────────────────
# metric -> (label, unit, source)
METRICS: Dict[str, Tuple[str, str, str]] = {
    "slope_deg":      ("Mean slope", "°", "Copernicus DEM GLO-30"),
    "cropland_pct":   ("Cropland share", "%", "ESA WorldCover 2021"),
    "builtup_pct":    ("Built-up share", "%", "ESA WorldCover 2021"),
    "tree_pct":       ("Tree-cover share", "%", "ESA WorldCover 2021"),
    "water_pct":      ("Water share", "%", "ESA WorldCover 2021"),
    "night_light":    ("Night-light radiance (demand/activity proxy)", "nW/sr/cm²", "VIIRS DNB monthly"),
    "substation_km":  ("Nearest mapped substation", "km", "OpenStreetMap"),
    "road_km":        ("Nearest mapped major road (segment centre)", "km", "OpenStreetMap"),
    "tower_density":  ("Mapped telecom towers", "per km²", "OpenStreetMap"),
    "charger_count":  ("Mapped EV charging stations", "count", "OpenStreetMap"),
}

GEE_TERRAIN_METRICS = {"slope_deg"}
GEE_LULC_METRICS = {"cropland_pct", "builtup_pct", "tree_pct", "water_pct"}
GEE_NIGHT_METRICS = {"night_light"}
OSM_KIND_FOR_METRIC = {"substation_km": "substation", "road_km": "road",
                       "tower_density": "tower", "charger_count": "charger"}
# query radius per OSM kind, km. Distance kinds use a radius >= every profile's "bad_at"
# so "none found within R" is a true lower bound, not an artefact of a short search.
OSM_RADIUS_KM = {"substation": 30.0, "road": 15.0, "tower": 5.0, "charger": 5.0}
# For count-type kinds, OSM must show at least this many features across ALL candidates
# before "zero near this candidate" is treated as a real signal rather than missing data.
OSM_MIN_TOTAL_FOR_ZERO_TO_COUNT = {"tower": 5, "charger": 3}

# metric -> (good_at, bad_at, weight). Utility is 1.0 at/beyond good_at, 0.0 at/beyond
# bad_at, linear between; direction is implied by the order of the two numbers.
PROFILES: Dict[str, Dict[str, Any]] = {
    "solar_farm": {
        "label": "Solar farm",
        "criteria": {
            "slope_deg":     (2, 10, 0.25),
            "cropland_pct":  (10, 60, 0.15),
            "tree_pct":      (5, 30, 0.10),
            "water_pct":     (2, 20, 0.05),
            "builtup_pct":   (5, 40, 0.10),
            "substation_km": (5, 30, 0.20),
            "road_km":       (2, 15, 0.15),
        },
        "flags": [("water_pct", ">=", 40, "Largely water-covered"),
                  ("slope_deg", ">=", 12, "Steep terrain"),
                  ("builtup_pct", ">=", 60, "Mostly built-up")],
    },
    "mobile_tower": {
        "label": "Mobile tower (coverage-gap siting)",
        "criteria": {
            "night_light":   (15, 1, 0.30),
            "builtup_pct":   (40, 5, 0.25),
            "tower_density": (0.3, 2.0, 0.25),
            "road_km":       (1, 10, 0.10),
            "water_pct":     (5, 40, 0.10),
        },
        "flags": [("water_pct", ">=", 50, "Largely water-covered")],
    },
    "warehouse_logistics": {
        "label": "Warehouse / logistics hub",
        "criteria": {
            "road_km":       (1, 10, 0.30),
            "slope_deg":     (3, 10, 0.15),
            "night_light":   (8, 0.5, 0.20),
            "cropland_pct":  (10, 60, 0.10),
            "water_pct":     (5, 30, 0.10),
            "substation_km": (5, 30, 0.15),
        },
        "flags": [("water_pct", ">=", 40, "Flood-prone / water-covered")],
    },
    "cold_storage": {
        "label": "Cold storage / cold chain",
        "criteria": {
            "road_km":       (1, 10, 0.25),
            "substation_km": (5, 30, 0.20),
            "cropland_pct":  (30, 5, 0.15),      # higher = larger farm catchment
            "night_light":   (5, 0.5, 0.10),
            "slope_deg":     (3, 10, 0.10),
            "water_pct":     (5, 30, 0.10),
            "builtup_pct":   (30, 70, 0.10),     # space availability
        },
        "flags": [("water_pct", ">=", 40, "Flood-prone / water-covered")],
    },
    "ev_charging": {
        "label": "EV charging station",
        "criteria": {
            "night_light":   (15, 1, 0.30),
            "builtup_pct":   (40, 5, 0.25),
            "road_km":       (1, 8, 0.25),
            "charger_count": (0, 5, 0.20),       # fewer existing = bigger gap
        },
        "flags": [],
    },
}

USE_CASE_KEYWORDS = [
    ("solar_farm", ("solar", "photovoltaic", "pv plant")),
    ("mobile_tower", ("mobile tower", "cell tower", "telecom tower", "cell site", "4g tower", "5g tower", "network tower", "mobile towers")),
    ("cold_storage", ("cold storage", "cold chain", "cold-chain")),
    ("warehouse_logistics", ("warehouse", "logistics hub", "godown", "distribution centre", "distribution center", "fulfilment", "fulfillment")),
    ("ev_charging", ("ev charging", "charging station", "charging stations", "ev charger")),
]


def guess_use_case(question: str) -> Optional[str]:
    q = question.lower()
    for key, words in USE_CASE_KEYWORDS:
        if any(w in q for w in words):
            return key
    return None


# ── scoring (pure functions) ──────────────────────────────────────────────────
def utility(value: float, good_at: float, bad_at: float) -> float:
    if good_at == bad_at:
        return 1.0
    t = (value - bad_at) / (good_at - bad_at)
    return max(0.0, min(1.0, t))


def _flag_hits(profile: dict, metrics: Dict[str, Optional[float]]) -> List[str]:
    out = []
    for metric, op, thr, text in profile.get("flags", []):
        v = metrics.get(metric)
        if v is None:
            continue
        if (op == ">=" and v >= thr) or (op == "<=" and v <= thr):
            out.append(f"{text} ({METRICS[metric][0].split(' (')[0].lower()} {v:g}{METRICS[metric][1]})")
    return out


def score_candidate(use_case: str, metrics: Dict[str, Optional[float]],
                    notes: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    profile = PROFILES[use_case]
    notes = notes or {}
    criteria, total_w, avail_w, acc = [], 0.0, 0.0, 0.0
    for metric, (good, bad, w) in profile["criteria"].items():
        label, unit, source = METRICS[metric]
        total_w += w
        v = metrics.get(metric)
        u = None if v is None else utility(v, good, bad)
        if u is not None:
            avail_w += w
            acc += w * u
        criteria.append({
            "metric": metric, "label": label, "unit": unit, "source": source,
            "value": None if v is None else round(v, 3), "utility": None if u is None else round(u, 3),
            "weight": w, "good_at": good, "bad_at": bad, "note": notes.get(metric, ""),
        })
    coverage = avail_w / total_w if total_w else 0.0
    score = round(100.0 * acc / avail_w, 1) if (avail_w and coverage >= MIN_COVERAGE_TO_SCORE) else None
    level = "strong" if coverage >= 0.8 else "partial" if coverage >= MIN_COVERAGE_TO_SCORE else "weak"
    return {"score": score, "evidence_coverage": round(coverage, 2), "evidence_level": level,
            "criteria": criteria, "flags": _flag_hits(profile, metrics)}


def rank_candidates(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    scored = sorted([i for i in items if i["score"] is not None], key=lambda i: -i["score"])
    unscored = [i for i in items if i["score"] is None]
    for n, it in enumerate(scored, 1):
        it["rank"] = n
    for it in unscored:
        it["rank"] = None
    return scored + unscored


# ── geometry helpers ──────────────────────────────────────────────────────────
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _geom_center(geom: dict) -> Optional[Tuple[float, float]]:
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
    return ((min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2)


def _bbox_radius_km(aoi: dict) -> float:
    c = _geom_center(aoi)
    if not c:
        return 2.0
    pts: List[Tuple[float, float]] = []

    def walk(x):
        if isinstance(x, (list, tuple)) and len(x) >= 2 and all(isinstance(v, (int, float)) for v in x[:2]):
            pts.append((x[1], x[0]))
        elif isinstance(x, (list, tuple)):
            for y in x:
                walk(y)
    walk(aoi.get("coordinates"))
    return max(haversine_km(c[0], c[1], la, lo) for la, lo in pts) if pts else 2.0


# ── OpenStreetMap / Overpass ──────────────────────────────────────────────────
_OSM_FILTERS = {
    "substation": ['nwr["power"="substation"]{A};'],
    "road": ['way["highway"~"^(motorway|trunk|primary|secondary)$"]{A};'],
    "tower": ['nwr["man_made"~"^(tower|mast)$"]["tower:type"="communication"]{A};',
              'nwr["communication:mobile_phone"="yes"]{A};'],
    "charger": ['nwr["amenity"="charging_station"]{A};'],
}
_ROAD_CLASSES = {"motorway", "trunk", "primary", "secondary"}


def build_overpass_query(centers: List[Tuple[float, float]], kinds: Set[str]) -> str:
    parts = []
    for lat, lon in centers:
        for kind in sorted(kinds):
            around = f"(around:{int(OSM_RADIUS_KM[kind] * 1000)},{lat:.5f},{lon:.5f})"
            parts.extend(f.replace("{A}", around) for f in _OSM_FILTERS[kind])
    return "[out:json][timeout:30];\n(\n" + "\n".join(parts) + "\n);\nout center tags 4000;"


def parse_overpass(elements: List[dict]) -> Dict[str, List[Tuple[float, float]]]:
    """-> {kind: [(lat, lon), ...]} de-duplicated by OSM element."""
    out: Dict[str, List[Tuple[float, float]]] = {k: [] for k in _OSM_FILTERS}
    seen: Set[Tuple[str, int]] = set()
    for el in elements or []:
        key = (el.get("type", ""), el.get("id", -1))
        if key in seen:
            continue
        seen.add(key)
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        if lat is None or lon is None:
            continue
        tags = el.get("tags") or {}
        pt = (float(lat), float(lon))
        if tags.get("power") == "substation":
            out["substation"].append(pt)
        if tags.get("highway") in _ROAD_CLASSES:
            out["road"].append(pt)
        if (tags.get("man_made") in ("tower", "mast") and tags.get("tower:type") == "communication") \
                or tags.get("communication:mobile_phone") == "yes":
            out["tower"].append(pt)
        if tags.get("amenity") == "charging_station":
            out["charger"].append(pt)
    return out


async def fetch_osm(centers: List[Tuple[float, float]], kinds: Set[str], diag: dict) -> Optional[Dict[str, List[Tuple[float, float]]]]:
    if not centers or not kinds:
        return None
    query = build_overpass_query(centers, kinds)
    last = "unknown"
    for url in OVERPASS_URLS:
        try:
            async with httpx.AsyncClient(timeout=OSM_TIMEOUT_S, headers=_HEADERS) as client:
                resp = await client.post(url, data={"data": query})
                resp.raise_for_status()
                return parse_overpass(resp.json().get("elements", []))
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            logger.warning(f"Siting agent: Overpass {url} failed: {last}")
    diag["osm_error"] = last
    return None


def _osm_metrics_for(center: Tuple[float, float], osm: Dict[str, List[Tuple[float, float]]],
                     kinds: Set[str]) -> Tuple[Dict[str, float], Dict[str, str]]:
    """Per-candidate OSM metrics before the completeness guards."""
    m: Dict[str, float] = {}
    notes: Dict[str, str] = {}
    for kind in kinds:
        rad = OSM_RADIUS_KM[kind]
        d = sorted(haversine_km(center[0], center[1], la, lo) for la, lo in osm.get(kind, []))
        within = [x for x in d if x <= rad]
        if kind in ("substation", "road"):
            metric = "substation_km" if kind == "substation" else "road_km"
            if within:
                m[metric] = round(within[0], 2)
            else:
                m[metric] = rad          # lower bound: nothing mapped within `rad` km
                notes[metric] = f"none mapped within {rad:g} km (OSM may be incomplete); value is a lower bound"
        elif kind == "tower":
            m["tower_density"] = round(len(within) / (math.pi * rad ** 2), 3)
        elif kind == "charger":
            m["charger_count"] = float(len(within))
    return m, notes


def _apply_osm_guards(per_cand: List[Dict[str, Any]], osm: Optional[Dict[str, List[Tuple[float, float]]]],
                      kinds: Set[str], caveats: List[str]) -> None:
    """Don't let sparse OSM masquerade as evidence."""
    if osm is None:
        for kind in kinds:
            caveats.append(f"OpenStreetMap query failed; '{kind}' criteria were not scored.")
        return
    for kind in kinds:
        total = len(osm.get(kind, []))
        if kind in ("substation", "road"):
            if total == 0:
                metric = "substation_km" if kind == "substation" else "road_km"
                for c in per_cand:
                    c["metrics"].pop(metric, None)
                    c["notes"].pop(metric, None)
                caveats.append(f"No {kind}s are mapped in OpenStreetMap near any candidate, so the {kind}-distance criterion was not scored (unmapped is not the same as absent).")
        else:
            need = OSM_MIN_TOTAL_FOR_ZERO_TO_COUNT[kind]
            if total < need:
                metric = "tower_density" if kind == "tower" else "charger_count"
                for c in per_cand:
                    c["metrics"].pop(metric, None)
                caveats.append(f"OpenStreetMap maps only {total} {kind}(s) across all candidates (needs ≥{need} to be informative), so that criterion was not scored. OSM telecom/charger coverage is sparse in much of India.")


# ── Earth Engine wrappers (sync GEE calls pushed to threads) ──────────────────
def _rs():
    from . import gee_remote_sensing as rs     # lazy: keeps ee out of import time / tests
    return rs


async def _run_gee(fn: Callable, *args, sem: asyncio.Semaphore) -> Any:
    async with sem:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), GEE_TOOL_TIMEOUT_S)


async def _gee_terrain(aoi: dict, sem: asyncio.Semaphore) -> Dict[str, float]:
    r = await _run_gee(_rs().compute_terrain_analysis, aoi, sem=sem)
    return {"slope_deg": float(r["slope_degrees"]["mean"])}


async def _gee_lulc(aoi: dict, sem: asyncio.Semaphore) -> Dict[str, float]:
    r = await _run_gee(_rs().compute_lulc_classification, aoi, sem=sem)
    by_code = {c["code"]: c["pct_of_aoi"] for c in r.get("classes", [])}
    return {"cropland_pct": by_code.get(40, 0.0), "builtup_pct": by_code.get(50, 0.0),
            "tree_pct": by_code.get(10, 0.0), "water_pct": by_code.get(80, 0.0) + by_code.get(90, 0.0)}


async def _gee_night_lights(aoi: dict, sem: asyncio.Semaphore) -> Dict[str, float]:
    end = datetime.date.today() - datetime.timedelta(days=100)       # the VIIRS product lags real time
    start = end - datetime.timedelta(days=365)
    r = await _run_gee(_rs().compute_night_lights, aoi, start.isoformat(), end.isoformat(), sem=sem)
    v = r.get("mean_radiance_overall")
    if v is None:
        raise ValueError("no usable night-light months")
    return {"night_light": float(v)}


# ── candidates ────────────────────────────────────────────────────────────────
async def resolve_candidates(places: List[dict], region: Optional[str],
                             region_center: Optional[Tuple[float, float]], caveats: List[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for idx, p in enumerate(places[:MAX_CANDIDATES]):
        name = p.get("place_name")
        if not name:
            continue
        if idx:
            await asyncio.sleep(NOMINATIM_GAP_S)
        feat = await geocode_util.geocode_place(f"{name}, {region}" if region else name)
        center = _geom_center((feat or {}).get("geometry") or {})
        if not center:
            caveats.append(f"Could not locate '{name}' on the map, so it was not measured.")
            continue
        if region_center and haversine_km(*center, *region_center) > MAX_CANDIDATE_DISTANCE_FROM_REGION_KM:
            caveats.append(f"'{name}' geocoded more than {MAX_CANDIDATE_DISTANCE_FROM_REGION_KM} km from the target region (ambiguous name?), so it was not measured.")
            continue
        radius = max(1.0, min(float(p.get("radius_km") or 2.0), 4.0))
        out.append({"name": name, "lat": center[0], "lon": center[1], "radius_km": radius,
                    "aoi": geocode_util._point_to_circle_polygon(center[0], center[1], radius),
                    "web_reasoning": p.get("reasoning", ""), "web_sources": p.get("source_urls", []),
                    "web_confidence": p.get("confidence")})
    return out


def candidate_from_aoi(aoi: dict, name: str = "Selected area") -> Optional[Dict[str, Any]]:
    c = _geom_center(aoi)
    if not c:
        return None
    return {"name": name, "lat": c[0], "lon": c[1], "radius_km": round(max(1.0, min(_bbox_radius_km(aoi), 15.0)), 1),
            "aoi": aoi, "web_reasoning": "", "web_sources": [], "web_confidence": None}


# ── narrative ─────────────────────────────────────────────────────────────────
NARRATE_SYSTEM = """\
You explain a site-screening result for a decision-maker. A deterministic scoring
step has ALREADY ranked the candidates from measured data. You must not change,
re-rank or re-score them; you explain them.

Return ONLY JSON:
{"recommendation": string,        // 3-5 sentences: which to shortlist first and why, citing measured values
 "tradeoffs": [string, ...],      // 2-4 concrete trade-offs between the top candidates
 "conflicts": [string, ...],      // places where web claims disagree with the measurements (empty if none)
 "next_steps": [string, ...]}     // 2-4 specific on-the-ground checks (land records, grid-connection enquiry, site visit...)

Rules: use only the numbers provided; say plainly when evidence is partial or a criterion is missing;
this is pre-feasibility screening of ~1-4 km areas, not a plot-level decision; do not quote sources verbatim.
"""


def _strengths_weaknesses(item: dict, k: int = 2) -> Tuple[List[str], List[str]]:
    scored = [c for c in item["criteria"] if c["utility"] is not None]
    by_contrib = sorted(scored, key=lambda c: c["weight"] * c["utility"], reverse=True)
    best = [f"{c['label'].split(' (')[0].lower()} {c['value']:g}{c['unit']}" for c in by_contrib if c["utility"] >= 0.7][:k]
    worst = sorted(scored, key=lambda c: c["weight"] * (1 - c["utility"]), reverse=True)
    bad = [f"{c['label'].split(' (')[0].lower()} {c['value']:g}{c['unit']}" for c in worst if c["utility"] <= 0.4][:k]
    return best, bad


def _template_narrative(label: str, ranking: List[dict]) -> Dict[str, Any]:
    scored = [r for r in ranking if r["score"] is not None]
    if not scored:
        return {"recommendation": f"None of the candidates had enough measurable evidence to be scored for {label.lower()} use.",
                "tradeoffs": [], "conflicts": [], "next_steps": ["Retry with a different candidate list or a drawn area."]}
    top = scored[0]
    best, bad = _strengths_weaknesses(top)
    txt = f"Highest screening score for {label.lower()}: {top['name']} ({top['score']}/100, {top['evidence_level']} evidence)."
    if best:
        txt += " Strongest on " + ", ".join(best) + "."
    if bad:
        txt += " Weakest on " + ", ".join(bad) + "."
    if len(scored) > 1:
        txt += f" Next: {scored[1]['name']} ({scored[1]['score']}/100)."
    return {"recommendation": txt, "tradeoffs": [], "conflicts": [],
            "next_steps": ["Confirm land availability and ownership", "Check grid/road access on the ground"]}


async def _narrate(llm: Optional[Callable[..., Awaitable[dict]]], question: str, region: Optional[str],
                   label: str, ranking: List[dict], web_summary: str, key_points: List[dict]) -> Dict[str, Any]:
    if llm is None:
        return _template_narrative(label, ranking)
    compact = []
    for r in ranking:
        best, bad = _strengths_weaknesses(r)
        compact.append({"rank": r["rank"], "name": r["name"], "score": r["score"], "coverage": r["evidence_coverage"],
                        "strengths": best, "weaknesses": bad, "flags": r["flags"], "web_claim": (r.get("web_reasoning") or "")[:300],
                        "missing": [c["label"] for c in r["criteria"] if c["utility"] is None]})
    user = (f"Region: {region or 'n/a'}\nUse case: {label}\nQuestion: {question}\n"
            f"Web research summary: {web_summary[:900]}\nRanking (authoritative): {compact}")
    try:
        out = await llm(NARRATE_SYSTEM, user, 1800)
        if not str(out.get("recommendation", "")).strip():
            raise ValueError("empty recommendation")
        return {"recommendation": str(out["recommendation"]).strip(),
                "tradeoffs": [str(x) for x in (out.get("tradeoffs") or [])][:4],
                "conflicts": [str(x) for x in (out.get("conflicts") or [])][:4],
                "next_steps": [str(x) for x in (out.get("next_steps") or [])][:4]}
    except Exception as e:
        logger.warning(f"Siting agent: narrative LLM failed ({type(e).__name__}); using template")
        return _template_narrative(label, ranking)


# ── orchestration ─────────────────────────────────────────────────────────────
def _progress(cb: Optional[Callable[[str, int], None]], label: str, pct: int) -> None:
    if cb:
        try:
            cb(label, pct)
        except Exception:
            pass


METHODOLOGY = (
    "Screening score (0-100) = weighted average of per-criterion utilities (1 = favourable, 0 = unfavourable), "
    "computed from measured values; criteria with no data are excluded and weights renormalised, and a candidate "
    "needs ≥50% evidence coverage to be scored. Weights and good/bad thresholds are editable screening heuristics, "
    "not regulatory standards. Earth-observation values are averages over a ~1-4 km circle around the locality "
    "centre (terrain: Copernicus DEM; land cover: ESA WorldCover 2021; activity: VIIRS night lights); infrastructure "
    "distances come from OpenStreetMap, which is incomplete in much of India. This is pre-feasibility screening, "
    "not a plot-level assessment."
)


async def evaluate_candidates(*, question: str, region: Optional[str], region_center: Optional[Tuple[float, float]],
                              places: List[dict], aoi: Optional[dict], use_case: str,
                              llm: Optional[Callable[..., Awaitable[dict]]] = None,
                              progress: Optional[Callable[[str, int], None]] = None,
                              web_summary: str = "", key_points: Optional[List[dict]] = None,
                              diag: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    diag = diag if diag is not None else {}
    profile = PROFILES.get(use_case)
    if not profile:
        return None
    caveats: List[str] = []

    _progress(progress, "Locating candidate areas", 55)
    if aoi and not places:
        c = candidate_from_aoi(aoi)
        cands = [c] if c else []
    else:
        cands = await resolve_candidates(places, region, region_center, caveats)
    if not cands:
        diag["siting"] = {"status": "no_candidates", "caveats": caveats}
        return None

    wanted = set(profile["criteria"])
    need_terrain = bool(wanted & GEE_TERRAIN_METRICS)
    need_lulc = bool(wanted & GEE_LULC_METRICS)
    need_night = bool(wanted & GEE_NIGHT_METRICS)
    osm_kinds = {OSM_KIND_FOR_METRIC[m] for m in wanted if m in OSM_KIND_FOR_METRIC}

    per_cand = [{"cand": c, "metrics": {}, "notes": {}, "errors": []} for c in cands]
    sem = asyncio.Semaphore(GEE_CONCURRENCY)

    jobs: List[Tuple[int, str, Callable[[], Awaitable[Dict[str, float]]]]] = []
    for i, c in enumerate(cands):
        if need_terrain:
            jobs.append((i, "terrain", lambda c=c: _gee_terrain(c["aoi"], sem)))
        if need_lulc:
            jobs.append((i, "land cover", lambda c=c: _gee_lulc(c["aoi"], sem)))
        if need_night:
            jobs.append((i, "night lights", lambda c=c: _gee_night_lights(c["aoi"], sem)))

    done = {"n": 0}
    total = max(len(jobs), 1)

    async def run_job(i: int, tool: str, factory) -> None:
        try:
            per_cand[i]["metrics"].update(await factory())
        except Exception as e:
            per_cand[i]["errors"].append(f"{tool}: {type(e).__name__}: {str(e)[:120]}")
            logger.warning(f"Siting agent: {tool} failed for {cands[i]['name']}: {e}")
        done["n"] += 1
        _progress(progress, f"Satellite analysis {done['n']}/{total}", 58 + int(30 * done["n"] / total))

    centers = [(c["lat"], c["lon"]) for c in cands]
    _progress(progress, "Measuring terrain, land cover, activity and nearby infrastructure", 58)
    osm_task = asyncio.create_task(fetch_osm(centers, osm_kinds, diag)) if osm_kinds else None
    await asyncio.gather(*[run_job(i, t, f) for i, t, f in jobs])
    osm = await osm_task if osm_task else None

    if osm_kinds:
        if osm is not None:
            for pc in per_cand:
                m, n = _osm_metrics_for((pc["cand"]["lat"], pc["cand"]["lon"]), osm, osm_kinds)
                pc["metrics"].update(m)
                pc["notes"].update(n)
        _apply_osm_guards(per_cand, osm, osm_kinds, caveats)

    _progress(progress, "Scoring and ranking candidates", 92)
    items = []
    for pc in per_cand:
        c = pc["cand"]
        sc = score_candidate(use_case, pc["metrics"], pc["notes"])
        items.append({**sc, "name": c["name"], "lat": round(c["lat"], 5), "lon": round(c["lon"], 5),
                      "radius_km": c["radius_km"], "web_reasoning": c["web_reasoning"],
                      "web_sources": c["web_sources"], "errors": pc["errors"]})
        for e in pc["errors"]:
            caveats.append(f"{c['name']}: {e}")
    ranking = rank_candidates(items)

    for it in ranking:
        if it["score"] is None:
            caveats.append(f"{it['name']} had only {int(it['evidence_coverage'] * 100)}% evidence coverage, so it was not scored.")

    narrative = await _narrate(llm, question, region, profile["label"], ranking, web_summary, key_points or [])
    diag["siting"] = {"status": "ok", "use_case": use_case, "candidates": len(cands), "gee_jobs": len(jobs),
                      "osm": None if osm is None else {k: len(v) for k, v in osm.items()}}
    return {"use_case": use_case, "use_case_label": profile["label"], "ranking": ranking,
            "recommendation": narrative["recommendation"], "tradeoffs": narrative["tradeoffs"],
            "conflicts": narrative["conflicts"], "next_steps": narrative["next_steps"],
            "caveats": caveats, "methodology": METHODOLOGY}
