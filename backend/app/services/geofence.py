"""
geofence.py — "how close am I to a boundary / restricted zone?" for the marine agent.

Pure-Python geometry (no GIS dependency). Boundary data is NOT bundled — see
backend/app/data/geofences/README.md. With no data loaded the engine reports that
plainly; it never invents a boundary.

Distances use a local equirectangular projection around the query point, with long
segments densified to ~10 km so the straight-line-in-projection error stays small over
the tens-of-km ranges that matter for warnings.
"""

import json
import logging
import math
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data" / "geofences"
DEFAULT_WARN_KM = 25.0
DEFAULT_DANGER_KM = 8.0
DENSIFY_KM = 10.0
SEVERITY = {"BREACH": 3, "DANGER": 2, "WARNING": 1}

LonLat = Tuple[float, float]


def _km_per_deg(lat0: float) -> Tuple[float, float]:
    return 111.320 * math.cos(math.radians(lat0)), 110.574      # (per deg lon, per deg lat)


def _hav_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


def _densify(ring: List[LonLat]) -> List[LonLat]:
    out: List[LonLat] = []
    for (lo1, la1), (lo2, la2) in zip(ring, ring[1:]):
        out.append((lo1, la1))
        d = _hav_km(la1, lo1, la2, lo2)
        if d > DENSIFY_KM:
            k = int(math.ceil(d / DENSIFY_KM))
            for i in range(1, k):
                f = i / k
                out.append((lo1 + (lo2 - lo1) * f, la1 + (la2 - la1) * f))
    if ring:
        out.append(ring[-1])
    return out


def _dist_to_path_km(lat: float, lon: float, path: List[LonLat]) -> float:
    kx, ky = _km_per_deg(lat)
    pts = [((lo - lon) * kx, (la - lat) * ky) for lo, la in path]
    best = float("inf")
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        dx, dy = x2 - x1, y2 - y1
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / seg2))
        px, py = x1 + t * dx, y1 + t * dy
        best = min(best, math.hypot(px, py))
    if len(pts) == 1:
        best = math.hypot(*pts[0])
    return best


def _in_ring(lat: float, lon: float, ring: List[LonLat]) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


class Fence:
    def __init__(self, feature: Dict[str, Any]):
        props = feature.get("properties") or {}
        geom = feature.get("geometry") or {}
        self.name: str = props.get("name") or "Unnamed boundary"
        self.kind: str = props.get("kind") or "boundary"
        self.mode: str = props.get("mode") or "restricted"
        self.warn_km: float = float(props.get("warn_km", DEFAULT_WARN_KM))
        self.danger_km: float = float(props.get("danger_km", DEFAULT_DANGER_KM))
        self.source: str = props.get("source") or "source not stated"
        self.note: str = props.get("note") or ""
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []
        self.is_polygon = gtype in ("Polygon", "MultiPolygon")
        if gtype == "LineString":
            self.lines = [[tuple(c[:2]) for c in coords]]
            self.polys: List[List[List[LonLat]]] = []
        elif gtype == "MultiLineString":
            self.lines = [[tuple(c[:2]) for c in ln] for ln in coords]
            self.polys = []
        elif gtype == "Polygon":
            self.lines = []
            self.polys = [[[tuple(c[:2]) for c in ring] for ring in coords]]
        elif gtype == "MultiPolygon":
            self.lines = []
            self.polys = [[[tuple(c[:2]) for c in ring] for ring in poly] for poly in coords]
        else:
            raise ValueError(f"unsupported geometry type {gtype!r} for fence {self.name!r}")
        pts = [p for ln in self.lines for p in ln] + [p for poly in self.polys for ring in poly for p in ring]
        lons, lats = [p[0] for p in pts], [p[1] for p in pts]
        self.bbox = (min(lons), min(lats), max(lons), max(lats)) if pts else (0, 0, 0, 0)
        self._dense_lines = [_densify(list(ln)) for ln in self.lines]
        self._dense_rings = [[_densify(list(r)) for r in poly] for poly in self.polys]

    def _far(self, lat: float, lon: float) -> bool:
        """Cheap bbox rejection: even the nearest bbox edge is beyond warn range (and we're outside)."""
        kx, ky = _km_per_deg(lat)
        minx, miny, maxx, maxy = self.bbox
        dx = max(minx - lon, 0, lon - maxx) * kx
        dy = max(miny - lat, 0, lat - maxy) * ky
        return math.hypot(dx, dy) > self.warn_km + 1.0

    def check(self, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        inside: Optional[bool] = None
        if self.is_polygon:
            inside = any(
                _in_ring(lat, lon, list(poly[0])) and not any(_in_ring(lat, lon, list(h)) for h in poly[1:])
                for poly in self.polys
            )
        # Bbox shortcut: a position far from a *restricted* zone/line can't alert. But for a "permitted"
        # polygon, being outside it IS the breach however far away — so never shortcut that case.
        if not inside and self._far(lat, lon) and not (self.is_polygon and self.mode == "permitted"):
            return None
        paths = self._dense_lines + [r for poly in self._dense_rings for r in poly]
        d = min((_dist_to_path_km(lat, lon, p) for p in paths if len(p) >= 1), default=float("inf"))

        level: Optional[str] = None
        if self.is_polygon:
            violating = inside if self.mode == "restricted" else (not inside)
            if violating:
                level = "BREACH"
        if level is None:
            level = "DANGER" if d <= self.danger_km else "WARNING" if d <= self.warn_km else None
        if level is None:
            return None

        if level == "BREACH":
            msg = (f"Inside restricted area: {self.name}." if self.mode == "restricted"
                   else f"Outside the permitted area: {self.name}.")
        else:
            msg = f"About {d:.0f} km from {self.name}."
        return {"name": self.name, "kind": self.kind, "level": level, "distance_km": round(d, 1),
                "inside": inside, "message": msg, "source": self.source, "note": self.note}


class GeofenceSet:
    def __init__(self, fences: Iterable[Fence] = (), loaded_from: Optional[str] = None):
        self.fences = list(fences)
        self.loaded_from = loaded_from

    @property
    def loaded(self) -> bool:
        return bool(self.fences)

    @property
    def names(self) -> List[str]:
        return [f.name for f in self.fences]

    def check(self, lat: float, lon: float) -> List[Dict[str, Any]]:
        alerts = [a for f in self.fences if (a := f.check(lat, lon))]
        return sorted(alerts, key=lambda a: (-SEVERITY[a["level"]], a["distance_km"]))

    @classmethod
    def from_geojson(cls, obj: Dict[str, Any], loaded_from: Optional[str] = None) -> "GeofenceSet":
        feats = obj.get("features") if obj.get("type") == "FeatureCollection" else [obj] if obj.get("type") == "Feature" else []
        fences = []
        for f in feats:
            try:
                fences.append(Fence(f))
            except Exception as e:
                logger.warning(f"Geofence feature skipped: {e}")
        return cls(fences, loaded_from)


def load_geofences(directory: Optional[os.PathLike] = None) -> GeofenceSet:
    d = Path(directory or os.environ.get("GEOFENCE_DIR") or DEFAULT_DIR)
    fences: List[Fence] = []
    if d.is_dir():
        for p in sorted(d.glob("*.geojson")):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    fences.extend(GeofenceSet.from_geojson(json.load(fh), str(p)).fences)
            except Exception as e:
                logger.warning(f"Could not read geofence file {p}: {e}")
    return GeofenceSet(fences, str(d))


_cached: Optional[GeofenceSet] = None


def get_geofences() -> GeofenceSet:
    global _cached
    if _cached is None:
        _cached = load_geofences()
    return _cached
