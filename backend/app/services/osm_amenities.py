"""
osm_amenities.py — "what is within reach of this spot?" from OpenStreetMap (free, via Overpass).

One Overpass request covers every location being compared. Per location we compute the nearest
hospital/clinic, school/college, supermarket/market, rail/metro station, bus stop, park and major road,
plus a few counts for context.

Honesty rules:
  * OSM is volunteer-mapped; coverage in India varies a lot. If a location has almost nothing mapped
    around it, we say OSM looks sparse there and DROP the amenity criteria instead of scoring a mapping
    gap as "no hospital nearby".
  * "None found within R km" is reported as a lower bound (distance >= R), never as zero.
  * Road distances use the segment centre (Overpass `out center`), which over-estimates the true
    distance to a long road — noted wherever it is shown.
  * Data (c) OpenStreetMap contributors, ODbL — attribution is part of the output.
  * Overpass reports a timed-out / out-of-memory query as HTTP 200 plus a `remark`; that is treated as a FAILURE,
    never as "nothing mapped".
  * Search is nearest-first in widening rings (<=1.5 km, <=3 km, full radius) so the per-statement result cap can
    never silently hide the nearest amenity in a dense city (Overpass cannot sort by distance).
"""

import asyncio
import logging
import math
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

logger = logging.getLogger(__name__)

OVERPASS_URLS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
_HEADERS = {"User-Agent": "VAYU-Location-Intelligence/1.0"}
TIMEOUT_S = 50
ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"

# kind -> (filters, search radius km, per-statement result cap)
KINDS: Dict[str, Tuple[List[str], float, int]] = {
    "hospital": (['nwr["amenity"~"^(hospital|clinic)$"]{A};'], 10.0, 400),
    "school": (['nwr["amenity"~"^(school|college|university)$"]{A};'], 6.0, 400),
    "market": (['nwr["shop"~"^(supermarket|mall|department_store)$"]{A};', 'nwr["amenity"="marketplace"]{A};'], 8.0, 400),
    "rail": (['nwr["railway"~"^(station|halt|subway_entrance|tram_stop)$"]{A};', 'nwr["public_transport"="station"]{A};'], 10.0, 300),
    "bus": (['node["highway"="bus_stop"]{A};'], 2.0, 400),
    "park": (['nwr["leisure"~"^(park|garden|playground)$"]{A};'], 5.0, 300),
    "road": (['way["highway"~"^(motorway|trunk|primary|secondary)$"]{A};'], 8.0, 400),
}
MIN_POIS_FOR_TRUST = 8      # across hospital+school+market+rail+bus+park near a location


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


RING_STEPS_KM = (1.5, 3.0)       # inner search rings; the last ring of every kind is its full radius
RING_GAP_S = 1.0                 # be polite to the public Overpass servers between rings
_FAIL_REMARKS = ("runtime error", "timed out", "out of memory")
_COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


class OverpassError(Exception):
    pass


def ring_radii(kind: str) -> List[float]:
    full = KINDS[kind][1]
    return sorted({min(full, r) for r in RING_STEPS_KM} | {full})


def build_query_pairs(pairs: List[Tuple[float, float, str, float]]) -> str:
    """pairs = (lat, lon, kind, radius_km). One `out` statement per kind, so each kind has its own result cap."""
    parts = ["[out:json][timeout:45];"]
    n = 0
    for kind, (filters, _full, cap) in KINDS.items():
        stmts = []
        for lat, lon, k, radius in pairs:
            if k != kind:
                continue
            around = f"(around:{int(radius * 1000)},{lat:.5f},{lon:.5f})"
            stmts.extend(f.replace("{A}", around) for f in filters)
        if stmts:
            parts.append("(\n" + "\n".join(stmts) + f"\n)->.k{n};\n.k{n} out center tags {cap};")
            n += 1
    return "\n".join(parts)


def build_query(centers: List[Tuple[float, float]]) -> str:
    """Full-radius query for every kind at every centre (kept for tests/diagnostics; fetch() uses rings)."""
    return build_query_pairs([(lat, lon, kind, KINDS[kind][1]) for lat, lon in centers for kind in KINDS])


def classify(el: Dict[str, Any]) -> Set[str]:
    t = el.get("tags") or {}
    kinds: Set[str] = set()
    if t.get("amenity") in ("hospital", "clinic"):
        kinds.add("hospital")
    if t.get("amenity") in ("school", "college", "university"):
        kinds.add("school")
    if t.get("shop") in ("supermarket", "mall", "department_store") or t.get("amenity") == "marketplace":
        kinds.add("market")
    if t.get("railway") in ("station", "halt", "subway_entrance", "tram_stop") or t.get("public_transport") == "station":
        kinds.add("rail")
    if t.get("highway") == "bus_stop":
        kinds.add("bus")
    if t.get("leisure") in ("park", "garden", "playground"):
        kinds.add("park")
    if t.get("highway") in ("motorway", "trunk", "primary", "secondary"):
        kinds.add("road")
    return kinds


def _label(el: Dict[str, Any]) -> Tuple[str, str]:
    t = el.get("tags") or {}
    name = (t.get("name:en") or t.get("name") or t.get("operator") or t.get("ref") or "").strip()
    sub = t.get("amenity") or t.get("shop") or t.get("railway") or t.get("public_transport") or t.get("leisure") or t.get("highway") or ""
    return name[:80], str(sub).replace("_", " ")


def parse(elements: List[Dict[str, Any]], pool: Optional[Dict[str, list]] = None, seen: Optional[Set[Tuple[str, int]]] = None
          ) -> Dict[str, list]:
    """-> {kind: [(lat, lon, name, subtype), ...]} de-duplicated by OSM element (pool/seen let rings accumulate)."""
    out: Dict[str, list] = pool if pool is not None else {k: [] for k in KINDS}
    seen = seen if seen is not None else set()
    for el in elements or []:
        key = (el.get("type", ""), el.get("id", -1))
        if key in seen:
            continue
        seen.add(key)
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        if lat is None or lon is None:
            continue
        name, sub = _label(el)
        for k in classify(el):
            out[k].append((float(lat), float(lon), name, sub))
    return out


async def _post(query: str) -> List[Dict[str, Any]]:
    last = "unknown"
    for url in OVERPASS_URLS:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_S, headers=_HEADERS) as client:
                resp = await client.post(url, data={"data": query})
            if resp.status_code in (429, 504):
                logger.warning(f"Overpass {url}: HTTP {resp.status_code}")
                last = "the OpenStreetMap server is busy (too many requests)"
                continue
            resp.raise_for_status()
            data = resp.json()
            remark = str(data.get("remark") or "")
            if any(m in remark.lower() for m in _FAIL_REMARKS):
                logger.warning(f"Overpass {url}: remark {remark[:160]!r}")
                last = "the OpenStreetMap query did not complete (server overloaded)"
                logger.warning(f"Overpass {url}: {last}")
                continue
            return data.get("elements", []) or []
        except httpx.TimeoutException:
            last = "the OpenStreetMap server timed out"
        except Exception as e:
            logger.warning(f"Overpass {url}: {type(e).__name__}: {e}")
            last = "the OpenStreetMap server could not be reached"
        logger.warning(f"Overpass {url}: {last}")
    raise OverpassError(last)


def _nearest_within(center: Tuple[float, float], pts: list, radius: float) -> Optional[float]:
    ds = [haversine_km(center[0], center[1], p[0], p[1]) for p in pts]
    ds = [d for d in ds if d <= radius]
    return min(ds) if ds else None


async def fetch(centers: List[Tuple[float, float]], diag: Optional[dict] = None) -> Optional[Dict[str, list]]:
    """Nearest-first ring search. None => OpenStreetMap could not be read at all (diag['osm_error'] says why).
    If only a LATER ring fails, what was found is returned and diag['osm_partial'] names the gap."""
    if not centers:
        return None
    pool: Dict[str, list] = {k: [] for k in KINDS}
    seen: Set[Tuple[str, int]] = set()
    open_pairs = {(i, k) for i in range(len(centers)) for k in KINDS}
    for rung in range(len(RING_STEPS_KM) + 1):
        pairs, owners = [], []
        for (i, k) in sorted(open_pairs):
            radii = ring_radii(k)
            if rung < len(radii):
                pairs.append((centers[i][0], centers[i][1], k, radii[rung]))
                owners.append(i)
        if not pairs:
            break
        if rung:
            await asyncio.sleep(RING_GAP_S)
        try:
            elements = await _post(build_query_pairs(pairs))
        except OverpassError as e:
            if rung == 0:
                if diag is not None:
                    diag["osm_error"] = str(e)
                return None
            if diag is not None:
                diag["osm_partial"] = str(e)
                diag["osm_unverified"] = {i: sorted({k for (j, k) in open_pairs if j == i}) for i in range(len(centers))}
            break
        parse(elements, pool, seen)
        for (lat, lon, k, radius), i in zip(pairs, owners):
            if _nearest_within((lat, lon), pool[k], radius) is not None or radius >= KINDS[k][1]:
                open_pairs.discard((i, k))
    return pool


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    y = math.sin(math.radians(lon2 - lon1)) * math.cos(math.radians(lat2))
    x = math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(math.radians(lon2 - lon1))
    return _COMPASS[int(((math.degrees(math.atan2(y, x)) + 360) % 360 + 22.5) // 45) % 8]


def summarize(center: Tuple[float, float], osm: Dict[str, list]) -> Dict[str, Any]:
    """-> {"metrics", "notes", "sparse", "counts", "count_radius_km", "nearby"}. Distances in km.

    nearby[kind] = up to 3 nearest mapped features [{name, type, distance_km, direction}] — what the reader actually
    sees. counts[kind] = features within the ring that first found the nearest one (count_radius_km[kind]); it is a
    lower bound on what is mapped nearby, not a census.
    """
    lat, lon = center
    nearest: Dict[str, Optional[float]] = {}
    counts: Dict[str, int] = {}
    ring: Dict[str, float] = {}
    nearby: Dict[str, List[Dict[str, Any]]] = {}
    for kind, (_, full, _cap) in KINDS.items():
        rows = sorted(((haversine_km(lat, lon, p[0], p[1]), p) for p in osm.get(kind, [])), key=lambda r: r[0])
        within = [r for r in rows if r[0] <= full]
        nearest[kind] = round(within[0][0], 2) if within else None
        ring[kind] = next((r for r in ring_radii(kind) if within and within[0][0] <= r), full)
        counts[kind] = sum(1 for d, _ in within if d <= ring[kind])
        nearby[kind] = [{"name": (p[2] if len(p) > 2 else "") or "", "type": (p[3] if len(p) > 3 else "") or "",
                         "distance_km": round(d, 2), "direction": _bearing(lat, lon, p[0], p[1])} for d, p in within[:3]]

    kinds6 = ("hospital", "school", "market", "rail", "bus", "park")
    poi_total = sum(counts[k] for k in kinds6)
    kinds_found = sum(1 for k in kinds6 if nearest[k] is not None)
    # Sparse = almost nothing mapped. Rings stop counting once the nearest is found, so judge by how many kinds
    # have ANY mapped feature as well as by the raw total.
    sparse = poi_total < MIN_POIS_FOR_TRUST and kinds_found < 4
    metrics: Dict[str, float] = {}
    notes: Dict[str, str] = {}
    if not sparse:
        def lb(kind: str, metric: str):
            if nearest[kind] is not None:
                metrics[metric] = nearest[kind]
            else:
                metrics[metric] = KINDS[kind][1]
                notes[metric] = f"none mapped within {KINDS[kind][1]:g} km (may be unmapped); value is a lower bound"
        lb("hospital", "hospital_km")
        lb("school", "school_km")
        lb("market", "market_km")
        lb("park", "park_km")
        lb("road", "road_km")
        rail, bus = nearest["rail"], nearest["bus"]
        cands = [x for x in (rail, bus) if x is not None]
        if cands:
            metrics["transit_km"] = min(cands)
        else:
            metrics["transit_km"] = KINDS["rail"][1]
            notes["transit_km"] = "no rail/metro station or bus stop mapped nearby (may be unmapped); lower bound"
    return {"metrics": metrics, "notes": notes, "sparse": sparse, "nearby": nearby,
            "counts": dict(counts), "count_radius_km": {k: ring[k] for k in KINDS},
            "search_radius_km": {k: KINDS[k][1] for k in KINDS}}
