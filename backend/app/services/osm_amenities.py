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
"""

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


def build_query(centers: List[Tuple[float, float]]) -> str:
    parts = ["[out:json][timeout:60];"]
    n = 0
    for kind, (filters, radius, cap) in KINDS.items():
        stmts = []
        for lat, lon in centers:
            around = f"(around:{int(radius * 1000)},{lat:.5f},{lon:.5f})"
            stmts.extend(f.replace("{A}", around) for f in filters)
        parts.append("(\n" + "\n".join(stmts) + f"\n)->.k{n};\n.k{n} out center tags {cap};")
        n += 1
    return "\n".join(parts)


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


def parse(elements: List[Dict[str, Any]]) -> Dict[str, List[Tuple[float, float]]]:
    out: Dict[str, List[Tuple[float, float]]] = {k: [] for k in KINDS}
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
        for k in classify(el):
            out[k].append((float(lat), float(lon)))
    return out


async def fetch(centers: List[Tuple[float, float]], diag: Optional[dict] = None) -> Optional[Dict[str, List[Tuple[float, float]]]]:
    if not centers:
        return None
    q = build_query(centers)
    last = "unknown"
    for url in OVERPASS_URLS:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_S, headers=_HEADERS) as client:
                resp = await client.post(url, data={"data": q})
            if resp.status_code == 429:
                last = "OpenStreetMap server is busy (HTTP 429)"
                continue
            resp.raise_for_status()
            return parse(resp.json().get("elements", []))
        except httpx.TimeoutException:
            last = "OpenStreetMap server timed out"
        except Exception as e:
            last = f"OpenStreetMap request failed ({type(e).__name__})"
        logger.warning(f"Overpass {url}: {last}")
    if diag is not None:
        diag["osm_error"] = last
    return None


def summarize(center: Tuple[float, float], osm: Dict[str, List[Tuple[float, float]]]) -> Dict[str, Any]:
    """-> {"metrics": {...}, "notes": {...}, "counts": {...}, "sparse": bool}. Distances in km."""
    lat, lon = center
    nearest: Dict[str, Optional[float]] = {}
    counts: Dict[str, int] = {}
    for kind, (_, radius, _) in KINDS.items():
        d = sorted(haversine_km(lat, lon, la, lo) for la, lo in osm.get(kind, []))
        within = [x for x in d if x <= radius]
        counts[kind] = len(within)
        nearest[kind] = round(within[0], 2) if within else None

    poi_total = sum(counts[k] for k in ("hospital", "school", "market", "rail", "bus", "park"))
    sparse = poi_total < MIN_POIS_FOR_TRUST
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
    return {"metrics": metrics, "notes": notes, "sparse": sparse,
            "counts": {"hospitals_10km": counts["hospital"], "schools_6km": counts["school"], "markets_8km": counts["market"],
                       "rail_stations_10km": counts["rail"], "bus_stops_2km": counts["bus"], "parks_5km": counts["park"]}}
