"""
In-memory vessel tracking store.

Unlike intel events (discrete, append-only), vessels are continuously moving
entities with a single current state per MMSI. This store overwrites the
latest known position/metadata per vessel and prunes anything not heard
from in STALE_MINUTES.

AIS ship type codes (per ITU-R M.1371) collapsed into broad categories
relevant to a "logistics/resources" framing:
  80-89  -> TANKER     (oil, chemicals, gas)
  70-79  -> CARGO      (bulk carriers — ore, grain, minerals, containers)
  60-69  -> PASSENGER
  30-39  -> FISHING
  everything else -> OTHER
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

STALE_MINUTES = 30
MAX_VESSELS = 5000


def classify_ship_type(type_code: Optional[int]) -> str:
    if type_code is None:
        return "OTHER"
    try:
        code = int(type_code)
    except (TypeError, ValueError):
        return "OTHER"
    if 80 <= code <= 89:
        return "TANKER"
    if 70 <= code <= 79:
        return "CARGO"
    if 60 <= code <= 69:
        return "PASSENGER"
    if 30 <= code <= 39:
        return "FISHING"
    return "OTHER"


CATEGORY_LABELS = {
    "TANKER": "Tanker (oil / chemical / gas)",
    "CARGO": "Cargo / Bulk Carrier (ore, grain, minerals, containers)",
    "PASSENGER": "Passenger Vessel",
    "FISHING": "Fishing Vessel",
    "OTHER": "Other / Unclassified",
}


class VesselStore:
    def __init__(self):
        self._vessels: dict[int, dict] = {}   # mmsi -> vessel dict
        self._lock = asyncio.Lock()
        self._stats = {"total_position_updates": 0, "total_static_updates": 0}

    async def update_position(
        self, mmsi: int, lat: float, lon: float,
        sog: float = 0.0, cog: float = 0.0, heading: float = None,
    ):
        async with self._lock:
            v = self._vessels.get(mmsi, {"mmsi": mmsi})
            v.update({
                "lat": lat, "lon": lon, "sog": sog, "cog": cog,
                "heading": heading, "last_update": datetime.utcnow().isoformat() + "Z",
            })
            self._vessels[mmsi] = v
            self._stats["total_position_updates"] += 1

            if len(self._vessels) > MAX_VESSELS:
                # Drop oldest stale entries first
                await self._prune_locked()

    async def update_static(
        self, mmsi: int, name: str = "", ship_type: int = None,
        destination: str = "", callsign: str = "",
    ):
        async with self._lock:
            v = self._vessels.get(mmsi, {"mmsi": mmsi})
            category = classify_ship_type(ship_type)
            v.update({
                "name": name.strip() if name else v.get("name", f"MMSI {mmsi}"),
                "ship_type_code": ship_type,
                "category": category,
                "category_label": CATEGORY_LABELS.get(category, "Other"),
                "destination": destination.strip() if destination else v.get("destination", ""),
                "callsign": callsign.strip() if callsign else v.get("callsign", ""),
            })
            self._vessels[mmsi] = v
            self._stats["total_static_updates"] += 1

    async def _prune_locked(self):
        cutoff = datetime.utcnow() - timedelta(minutes=STALE_MINUTES)
        before = len(self._vessels)
        self._vessels = {
            mmsi: v for mmsi, v in self._vessels.items()
            if "last_update" in v and
            datetime.fromisoformat(v["last_update"].rstrip("Z")) > cutoff
        }
        pruned = before - len(self._vessels)
        if pruned:
            logger.info(f"VesselStore: pruned {pruned} stale vessels")

    async def prune_stale(self):
        async with self._lock:
            await self._prune_locked()

    def query(
        self,
        category: Optional[str] = None,
        bbox: Optional[tuple[float, float, float, float]] = None,
        limit: int = 1000,
    ) -> list[dict]:
        results = []
        for v in self._vessels.values():
            if "lat" not in v or "lon" not in v:
                continue
            if category and v.get("category") != category.upper():
                continue
            if bbox:
                min_lat, min_lon, max_lat, max_lon = bbox
                if not (min_lat <= v["lat"] <= max_lat and min_lon <= v["lon"] <= max_lon):
                    continue
            results.append(v)
            if len(results) >= limit:
                break
        return results

    def get_stats(self) -> dict:
        by_category = {}
        for v in self._vessels.values():
            cat = v.get("category", "OTHER")
            by_category[cat] = by_category.get(cat, 0) + 1
        return {
            **self._stats,
            "active_vessels": len([v for v in self._vessels.values() if "lat" in v]),
            "by_category": by_category,
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
vessel_store = VesselStore()
