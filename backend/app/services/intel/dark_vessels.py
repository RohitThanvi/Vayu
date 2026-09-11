"""
dark_vessels.py — flags vessels that go dark (AIS transponder stops
reporting) near a monitored chokepoint and then reappear somewhere
else. This is a real signal used in actual maritime intelligence for
sanctions evasion / smuggling ("AIS gap" behavior) — a vessel disabling
its transponder to hide a port call or ship-to-ship transfer.

No new data source: this runs entirely on the vessel positions the AIS
bridge already streams in (see vessel_store.py). It's pure logic —
diffing consecutive snapshots — which is exactly why it's cheap to add.

How it works, each time a fresh vessel snapshot lands:
  1. Any vessel that was within GAP_TRIGGER_KM of a chokepoint in the
     previous snapshot, and is missing from the new one, goes into a
     `_vanished` watchlist with its last known position/time.
  2. Any vessel that reappears (present in a new snapshot) while on
     that watchlist gets checked: if the gap was >= MIN_GAP_MINUTES
     and it moved more than IMPLAUSIBLE_DRIFT_KM further than its
     max plausible speed could explain in that time, it's flagged.
  3. Watchlist entries older than MAX_WATCH_HOURS are dropped (vessel
     presumably left the area, or AIS bridge lost it for an unrelated
     reason — see the false-positive guard below).

False-positive guard: if a large fraction of ALL tracked vessels
vanish in the same snapshot (a bridge reconnect/outage, not a real
gap), none of them are added to the watchlist that cycle — this
mirrors the "suspicious shrink" guard vessel_store.py already applies
to whole-snapshot drops, for the same reason (Render free-tier /
bridge reconnect blips look identical to a real mass-disappearance).
"""

import logging
import math
from datetime import datetime, timedelta

from .vessel_store import CHOKEPOINTS

logger = logging.getLogger(__name__)

GAP_TRIGGER_KM = 60            # "near a chokepoint" radius to start watching a vessel
MIN_GAP_MINUTES = 25           # minimum dark period before it's worth flagging at all
MAX_WATCH_HOURS = 18           # give up watching for a reappearance after this long
IMPLAUSIBLE_DRIFT_KM_PER_HR = 45  # a loitering/transiting vessel's realistic ceiling;
                                    # reappearing further than this in the gap window
                                    # is the actual "AIS gap" tell, not just normal transit
MASS_VANISH_FRACTION = 0.35    # if >35% of tracked vessels vanish in one snapshot,
                                # treat it as a bridge blip, not real gaps

_vanished: dict[int, dict] = {}     # mmsi -> {since, lat, lon, chokepoint, name, category}
_flags: list[dict] = []             # recent confirmed dark-vessel flags, newest first
MAX_FLAGS = 200


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _nearest_chokepoint(lat: float, lon: float) -> tuple[str, float] | tuple[None, None]:
    best_id, best_km = None, None
    for cp_id, ((lat1, lon1), (lat2, lon2)) in CHOKEPOINTS.items():
        c_lat, c_lon = (lat1 + lat2) / 2, (lon1 + lon2) / 2
        d = _haversine_km(lat, lon, c_lat, c_lon)
        if best_km is None or d < best_km:
            best_id, best_km = cp_id, d
    return (best_id, best_km) if best_km is not None else (None, None)


def process_snapshot(old_vessels: dict[int, dict], new_vessels: dict[int, dict]):
    """Call this with the vessel_store's dicts (mmsi -> vessel) right
    before/after a load_snapshot() replace. Pure bookkeeping — never
    raises, so a bug here can't take down vessel ingestion."""
    try:
        _process(old_vessels, new_vessels)
    except Exception as e:
        logger.error(f"dark_vessels: snapshot processing failed — {type(e).__name__}: {e}")


def _process(old_vessels: dict[int, dict], new_vessels: dict[int, dict]):
    now = datetime.utcnow()

    if old_vessels:
        missing = set(old_vessels) - set(new_vessels)
        vanish_fraction = len(missing) / max(len(old_vessels), 1)
        mass_blip = vanish_fraction > MASS_VANISH_FRACTION and len(old_vessels) >= 15
        if mass_blip:
            logger.info(
                f"dark_vessels: {len(missing)}/{len(old_vessels)} vessels vanished this "
                f"snapshot ({vanish_fraction:.0%}) — treating as a bridge blip, not tracking as gaps"
            )
        else:
            for mmsi in missing:
                v = old_vessels[mmsi]
                if "lat" not in v or "lon" not in v:
                    continue
                cp_id, dist = _nearest_chokepoint(v["lat"], v["lon"])
                if cp_id is None or dist > GAP_TRIGGER_KM:
                    continue
                if mmsi in _vanished:
                    continue
                _vanished[mmsi] = {
                    "since": now, "lat": v["lat"], "lon": v["lon"],
                    "chokepoint": cp_id, "name": v.get("name", f"MMSI {mmsi}"),
                    "category": v.get("category", "OTHER"),
                }

    # Reappearances
    for mmsi, v in new_vessels.items():
        watch = _vanished.get(mmsi)
        if not watch or "lat" not in v or "lon" not in v:
            continue
        gap = now - watch["since"]
        gap_minutes = gap.total_seconds() / 60
        del _vanished[mmsi]  # resolved either way — flagged or not, stop watching
        if gap_minutes < MIN_GAP_MINUTES:
            continue
        drift_km = _haversine_km(watch["lat"], watch["lon"], v["lat"], v["lon"])
        plausible_km = IMPLAUSIBLE_DRIFT_KM_PER_HR * (gap_minutes / 60)
        if drift_km <= plausible_km:
            continue  # a real, continuously-tracked vessel could have covered this distance anyway
        _flags.insert(0, {
            "mmsi": mmsi,
            "name": watch["name"],
            "category": watch["category"],
            "chokepoint": watch["chokepoint"],
            "vanished_at": watch["since"].isoformat() + "Z",
            "reappeared_at": now.isoformat() + "Z",
            "gap_minutes": round(gap_minutes, 1),
            "last_known": {"lat": watch["lat"], "lon": watch["lon"]},
            "reappeared_at_position": {"lat": v["lat"], "lon": v["lon"]},
            "drift_km": round(drift_km, 1),
            "note": (
                f"{watch['name']} went dark near {watch['chokepoint'].replace('_', ' ').title()} "
                f"for {gap_minutes:.0f} min and reappeared {drift_km:.0f}km away — "
                f"further than continuous transit at a realistic speed would explain."
            ),
        })
        del _flags[MAX_FLAGS:]

    # Drop stale watches
    cutoff = now - timedelta(hours=MAX_WATCH_HOURS)
    for mmsi in [m for m, w in _vanished.items() if w["since"] < cutoff]:
        del _vanished[mmsi]


def list_flags(limit: int = 50) -> list[dict]:
    return _flags[:limit]


def get_stats() -> dict:
    return {"currently_watching": len(_vanished), "total_flags": len(_flags)}
