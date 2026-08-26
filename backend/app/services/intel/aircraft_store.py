"""
In-memory aircraft tracking store.

Same shape as vessel_store.py and for the same reason: aircraft are
continuously moving entities with a single current state per ICAO24 hex
address, not discrete append-only events. This store overwrites the latest
known position/metadata per aircraft and prunes anything not heard from in
STALE_MINUTES.

Source is OpenSky Network's free, keyless /api/states/all endpoint — a
single global snapshot per poll (see fetchers.py: fetch_opensky), so
load_snapshot() replaces the whole store each cycle the same way AIS
bridge polling does, including the same "don't trust a single anomalous
shrink" guard — OpenSky's anonymous tier is rate-limited and occasionally
returns a partial or empty state vector on a given poll even though the
network is fine.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

STALE_MINUTES = 10   # aircraft positions go stale fast — much shorter than vessels
MAX_AIRCRAFT = 8000


class AircraftStore:
    def __init__(self):
        self._aircraft: dict[str, dict] = {}   # icao24 -> aircraft dict
        self._lock = asyncio.Lock()
        self._stats = {"total_position_updates": 0}
        self._consecutive_shrinks = 0

    async def load_snapshot(self, aircraft: list[dict]):
        """Replace the whole store with a fresh OpenSky snapshot.

        Same anomalous-shrink guard as VesselStore.load_snapshot: OpenSky's
        anonymous/keyless tier can come back thin on an individual poll
        (rate limiting, upstream hiccup) without the network actually being
        down. A single suspicious shrink is treated as a skipped cycle;
        only accepted once confirmed on consecutive polls.
        """
        async with self._lock:
            new_count = len(aircraft)
            old_count = len(self._aircraft)

            suspicious_shrink = old_count >= 50 and new_count < old_count * 0.2
            if suspicious_shrink:
                self._consecutive_shrinks += 1
                if self._consecutive_shrinks < 3:
                    logger.warning(
                        f"AircraftStore: snapshot shrank {old_count} -> {new_count} "
                        f"(consecutive={self._consecutive_shrinks}/3) — treating as a "
                        f"transient OpenSky blip, keeping last-known-good data this cycle"
                    )
                    return
                logger.warning(
                    f"AircraftStore: snapshot shrink {old_count} -> {new_count} confirmed "
                    f"over {self._consecutive_shrinks} consecutive polls, accepting it"
                )

            self._consecutive_shrinks = 0
            self._aircraft = {a["icao24"]: a for a in aircraft if "icao24" in a}
            self._stats["total_position_updates"] += len(self._aircraft)

            if len(self._aircraft) > MAX_AIRCRAFT:
                await self._prune_locked()

    async def _prune_locked(self):
        cutoff = datetime.utcnow() - timedelta(minutes=STALE_MINUTES)
        before = len(self._aircraft)
        self._aircraft = {
            icao24: a for icao24, a in self._aircraft.items()
            if "last_update" in a and
            datetime.fromisoformat(a["last_update"].rstrip("Z")) > cutoff
        }
        pruned = before - len(self._aircraft)
        if pruned:
            logger.info(f"AircraftStore: pruned {pruned} stale aircraft")

    async def prune_stale(self):
        async with self._lock:
            await self._prune_locked()

    def query(
        self,
        bbox: Optional[tuple[float, float, float, float]] = None,
        on_ground: Optional[bool] = None,
        limit: int = 2000,
    ) -> list[dict]:
        results = []
        for a in self._aircraft.values():
            if "lat" not in a or "lon" not in a:
                continue
            if on_ground is not None and a.get("on_ground") != on_ground:
                continue
            if bbox:
                min_lat, min_lon, max_lat, max_lon = bbox
                if not (min_lat <= a["lat"] <= max_lat and min_lon <= a["lon"] <= max_lon):
                    continue
            results.append(a)
            if len(results) >= limit:
                break
        return results

    def get_stats(self) -> dict:
        airborne = len([a for a in self._aircraft.values() if "lat" in a and not a.get("on_ground")])
        on_ground = len([a for a in self._aircraft.values() if "lat" in a and a.get("on_ground")])
        return {
            **self._stats,
            "active_aircraft": len([a for a in self._aircraft.values() if "lat" in a]),
            "airborne": airborne,
            "on_ground": on_ground,
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
aircraft_store = AircraftStore()
