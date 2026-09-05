"""
geocode_util.py — place name -> AOI polygon, via Nominatim (OpenStreetMap).
Shared by the agri WhatsApp bot (services/agri/whatsapp.py, which
originally had its own private copy of this) and the main query
endpoint's AOI auto-resolution (see api/endpoints.py step 2) — a plain
region name in a query like "vegetation change in Jaipur" can now
resolve to a real AOI without the user manually drawing one.

Nominatim usually returns an actual administrative boundary polygon
for named places (city, district) when polygon_geojson=1 is requested,
but falls back to a bare Point for smaller/less-mapped places. A Point
can't be fed into the GEE metric pipeline (it needs an area), so
_point_to_circle_polygon manually constructs a small circular polygon
around it — plain trigonometry, no new dependency (shapely isn't
already in requirements.txt, and pulling it in just for this one call
isn't worth the added install weight on Render's free tier, especially
given this project's history of free-tier infra friction).
"""

import logging
import math
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_HEADERS = {"User-Agent": "VAYU-Geocode/1.0"}

# Matches the "2km" convention already used elsewhere in this project
# (research agent's default marker radius) — a sane default AOI size
# for a bare place name with no drawn boundary and no explicit radius.
DEFAULT_FALLBACK_RADIUS_KM = 2.0


def _point_to_circle_polygon(lat: float, lon: float, radius_km: float, n_points: int = 32) -> Dict[str, Any]:
    """Plain-trig circle approximation as a GeoJSON Polygon — no shapely needed."""
    coords = []
    lat_rad = math.radians(lat)
    for i in range(n_points + 1):   # +1 to close the ring
        angle = 2 * math.pi * i / n_points
        d_lat = (radius_km / 111.0) * math.cos(angle)
        d_lon = (radius_km / (111.0 * max(math.cos(lat_rad), 0.01))) * math.sin(angle)
        coords.append([lon + d_lon, lat + d_lat])
    return {"type": "Polygon", "coordinates": [coords]}


async def geocode_place(query: str) -> Optional[dict]:
    """Nominatim geocode + boundary — same service the frontend search bar uses.
    Returns a raw GeoJSON Feature (geometry may be Polygon/MultiPolygon or a
    bare Point, depending on what OSM has mapped for this place) or None."""
    try:
        async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as client:
            resp = await client.get(
                NOMINATIM_URL,
                params={"q": query, "format": "geojson", "polygon_geojson": 1, "limit": 1},
            )
            resp.raise_for_status()
            data = resp.json()
            features = data.get("features", [])
            if not features:
                return None
            return features[0]
    except Exception as e:
        logger.warning(f"geocode_place failed for '{query}': {e}")
        return None


async def geocode_to_aoi(query: str, fallback_radius_km: float = DEFAULT_FALLBACK_RADIUS_KM) -> Optional[Dict[str, Any]]:
    """Geocode a place name straight to a GEE-ready AOI polygon. If
    Nominatim has a real boundary, use it as-is; if it only has a point,
    buffer that point into a small circular polygon so the 9-metric GEE
    pipeline (which needs an area, not a point) can still run. Returns
    None if the place can't be found at all."""
    feature = await geocode_place(query)
    if not feature or not feature.get("geometry"):
        return None

    geom = feature["geometry"]
    if geom.get("type") in ("Polygon", "MultiPolygon"):
        return geom

    if geom.get("type") == "Point":
        lon, lat = geom["coordinates"][0], geom["coordinates"][1]
        logger.info(f"geocode_to_aoi: '{query}' resolved to a bare point, buffering to a {fallback_radius_km}km circle")
        return _point_to_circle_polygon(lat, lon, fallback_radius_km)

    logger.warning(f"geocode_to_aoi: unexpected geometry type {geom.get('type')!r} for '{query}'")
    return None
