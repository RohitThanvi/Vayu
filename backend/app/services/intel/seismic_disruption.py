"""
seismic_disruption.py — ties USGS earthquake events (already ingested
into the intel store by fetchers.fetch_usgs) to the same chokepoint /
commodity mapping supply_chain.py already maintains, so a significant
quake near a chokepoint reads as a supply-chain disruption signal
instead of just an unrelated pin on the map.

No new data source: USGS is already polled. This is a proximity join
against CHOKEPOINTS + CHOKEPOINT_COMMODITIES, both already defined
elsewhere (vessel_store.py, supply_chain.py) and reused here rather
than redefined, so the two features can't silently drift out of sync.
"""

import math
from typing import Any

from .vessel_store import CHOKEPOINTS
from .supply_chain import CHOKEPOINT_COMMODITIES, CHOKEPOINT_DISPLAY

PROXIMITY_KM = 400  # a quake within this radius of a chokepoint is treated as
                      # a plausible physical-disruption risk to it (port/terminal
                      # damage, tsunami risk to shipping lanes, etc.) — generous
                      # on purpose since the actual affected radius depends on
                      # magnitude/depth in ways this simple join doesn't model


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def find_disruptions(usgs_events: list[dict], min_magnitude: float = 5.0) -> list[dict[str, Any]]:
    """usgs_events: events from IntelStore.query(sources=['USGS']).
    Returns one entry per (chokepoint, quake) pair within PROXIMITY_KM,
    for quakes at/above min_magnitude — smaller quakes are common and
    rarely disruptive, so they're filtered out here rather than left
    for the caller to re-filter."""
    hits = []
    for ev in usgs_events:
        mag = ev.get("meta", {}).get("magnitude")
        if mag is None or mag < min_magnitude:
            continue
        if "lat" not in ev or "lon" not in ev:
            continue
        for cp_id, ((lat1, lon1), (lat2, lon2)) in CHOKEPOINTS.items():
            c_lat, c_lon = (lat1 + lat2) / 2, (lon1 + lon2) / 2
            dist = _haversine_km(ev["lat"], ev["lon"], c_lat, c_lon)
            if dist > PROXIMITY_KM:
                continue
            hits.append({
                "chokepoint": cp_id,
                "chokepoint_display": CHOKEPOINT_DISPLAY.get(cp_id, cp_id),
                "watch_commodities": CHOKEPOINT_COMMODITIES.get(cp_id, []),
                "quake_title": ev.get("title", ""),
                "magnitude": mag,
                "distance_km": round(dist, 0),
                "quake_time": ev.get("ts", ""),
                "note": (
                    f"M{mag:.1f} quake {dist:.0f}km from {CHOKEPOINT_DISPLAY.get(cp_id, cp_id)} — "
                    f"worth watching {', '.join(CHOKEPOINT_COMMODITIES.get(cp_id, [])) or 'related commodities'} "
                    f"for a disruption-driven price move."
                ),
            })
    hits.sort(key=lambda h: h["distance_km"])
    return hits
