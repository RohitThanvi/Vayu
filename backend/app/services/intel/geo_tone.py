"""
geo_tone.py — aggregates the GDELT tone score (already extracted per-
event by fetchers.py -> event['meta']['tone']) into a regional
sentiment layer, instead of just showing GDELT's individual pins one
at a time.

No new data source or network call: this reads whatever GDELT events
are already sitting in the intel store (store.py) and bins them into
coarse lat/lon grid cells, giving a "geopolitical tone" score per
region rather than per single article. GDELT tone is a signed score,
roughly -10 (very negative coverage) to +10 (very positive); this
follows fetchers.py's own bands (< -5 critical, < -2 warn) for
consistency when labeling.

Grid cells rather than country lookup deliberately — reverse-geocoding
lat/lon to a country name needs either a paid API or a bundled offline
shapefile, neither of which is free-and-zero-setup; a coarse grid gets
the same "which region is trending negative" answer without adding a
dependency.
"""

from collections import defaultdict
from typing import Any

CELL_SIZE_DEG = 8.0  # ~coarse regional resolution — big enough to cluster related coverage,
                       # small enough that e.g. Middle East and East Asia don't merge


def _cell(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat / CELL_SIZE_DEG) * CELL_SIZE_DEG, round(lon / CELL_SIZE_DEG) * CELL_SIZE_DEG)


def compute_tone_regions(gdelt_events: list[dict], min_events: int = 2) -> list[dict[str, Any]]:
    """gdelt_events: events from IntelStore.query(sources=['GDELT']).
    Returns regions sorted by most-negative tone first (the
    "deteriorating" hotspots are the actionable ones)."""
    cells: dict[tuple, list[dict]] = defaultdict(list)
    for e in gdelt_events:
        meta = e.get("meta", {})
        if "tone" not in meta or "lat" not in e or "lon" not in e:
            continue
        cells[_cell(e["lat"], e["lon"])].append(e)

    regions = []
    for (clat, clon), events in cells.items():
        if len(events) < min_events:
            continue
        tones = [ev["meta"]["tone"] for ev in events]
        avg_tone = sum(tones) / len(tones)
        regions.append({
            "lat": clat, "lon": clon,
            "avg_tone": round(avg_tone, 2),
            "event_count": len(events),
            "label": "deteriorating" if avg_tone < -5 else "tense" if avg_tone < -2 else
                     "improving" if avg_tone > 5 else "neutral",
            "sample_themes": list({ev["meta"].get("theme", "") for ev in events if ev["meta"].get("theme")})[:5],
            "most_recent": max((ev.get("ts", "") for ev in events), default=""),
        })

    regions.sort(key=lambda r: r["avg_tone"])
    return regions
