"""
data_router.py — before falling back to a general web search (SerpApi),
check whether an out-of-scope question can be answered directly from
Vayu's OWN live intel feeds: AIS vessel tracking, ADS-B aircraft
tracking, USGS earthquakes, and NASA FIRMS fires (GDELT/ACLED are text
news-event feeds, not counters of a physical thing happening "right
now", so they're deliberately not wired into this counting-style
router — a web search genuinely serves a "what's the news on X" style
question better than a raw feed count would).

These are real, already-running sources this project maintains for its
own map layers (see services/intel/vessel_store.py, aircraft_store.py,
store.py) — this module doesn't add a new integration, it just routes
a qualifying question to data Vayu already has instead of a search
engine. "How many ships are crossing the Strait of Hormuz right now"
gets answered from actual live AIS positions, not a guess grounded in
old news articles about the strait.

Deliberately conservative about what it claims to answer:
- Maritime: only the 7 named chokepoints this project already monitors
  (see vessel_store.CHOKEPOINTS). A ship question about anywhere else
  falls through to web search rather than guessing a bounding box for
  an arbitrary place — this project doesn't track vessels globally,
  only inside those 7 zones (that's what the AIS bridge subscribes to).
- Aviation/earthquakes/fires: global counts only, and only for a
  clearly "how many / count / currently active" style question — not
  for "tell me about the earthquake in X" (that's a request for
  narrative/news content, which the live feed can't provide and web
  search legitimately can).
Anything not matching one of these falls through to None, and the
caller (research_agent.ask) proceeds to the normal SerpApi path exactly
as before.
"""

from typing import Any, Dict, Optional

from .intel.vessel_store import vessel_store, CHOKEPOINTS, CATEGORY_LABELS
from .intel.aircraft_store import aircraft_store
from .intel.store import intel_store

CHOKEPOINT_ALIASES = {
    "strait_of_hormuz":    ["hormuz"],
    "strait_of_malacca":   ["malacca"],
    "bab_el_mandeb":       ["bab el mandeb", "bab-el-mandeb", "mandeb"],
    "suez_canal":          ["suez"],
    "strait_of_gibraltar": ["gibraltar"],
    "panama_canal":        ["panama"],
    "english_channel":     ["english channel"],
}

CHOKEPOINT_DISPLAY = {
    "strait_of_hormuz":    "Strait of Hormuz",
    "strait_of_malacca":   "Strait of Malacca",
    "bab_el_mandeb":       "Bab-el-Mandeb Strait",
    "suez_canal":          "Suez Canal",
    "strait_of_gibraltar": "Strait of Gibraltar",
    "panama_canal":        "Panama Canal",
    "english_channel":     "English Channel",
}

MARITIME_WORDS   = ["ship", "ships", "vessel", "vessels", "tanker", "tankers", "cargo ship", "cargo ships", "maritime traffic", "boat traffic"]
AVIATION_WORDS   = ["aircraft", "airplane", "airplanes", "plane", "planes", "flight", "flights"]
EARTHQUAKE_WORDS = ["earthquake", "earthquakes", "seismic", "tremor", "tremors", "magnitude"]
FIRE_WORDS       = ["wildfire", "wildfires", "active fire", "active fires", "fires burning", "forest fire", "forest fires"]
COUNT_WORDS      = ["how many", "count", "number of", "currently", "right now", "active"]


def _match_chokepoint(text: str) -> Optional[str]:
    for key, aliases in CHOKEPOINT_ALIASES.items():
        if any(alias in text for alias in aliases):
            return key
    return None


def _bbox_center_and_radius(bbox):
    (min_lat, min_lon), (max_lat, max_lon) = bbox
    center_lat = (min_lat + max_lat) / 2
    center_lon = (min_lon + max_lon) / 2
    lat_km = (max_lat - min_lat) * 111
    lon_km = (max_lon - min_lon) * 111 * 0.87   # rough cos(lat) correction, fine at these latitudes
    return center_lat, center_lon, round(max(lat_km, lon_km) / 2, 1)


def try_answer_from_live_data(question: str) -> Optional[Dict[str, Any]]:
    q = question.lower()

    # ── Maritime (AIS) — only for the 7 named chokepoints already monitored ──
    if any(w in q for w in MARITIME_WORDS):
        choke_key = _match_chokepoint(q)
        if not choke_key:
            return None   # don't guess a bbox for an untracked region
        bbox_pair = CHOKEPOINTS[choke_key]
        (min_lat, min_lon), (max_lat, max_lon) = bbox_pair
        vessels = vessel_store.query(bbox=(min_lat, min_lon, max_lat, max_lon))
        by_cat: Dict[str, int] = {}
        for v in vessels:
            cat = v.get("category", "OTHER")
            by_cat[cat] = by_cat.get(cat, 0) + 1
        _, _, radius = _bbox_center_and_radius(bbox_pair)
        display_name = CHOKEPOINT_DISPLAY[choke_key]
        breakdown = ", ".join(
            f"{n} {CATEGORY_LABELS.get(c, c).lower()}"
            for c, n in sorted(by_cat.items(), key=lambda x: -x[1])
        ) or "none currently tracked"
        reasoning = (
            f"Live AIS tracking currently shows {len(vessels)} vessel(s) inside the "
            f"{display_name} monitored zone: {breakdown}. This is real-time position "
            f"data, not a web search result — vessel counts change continuously as "
            f"ships transit the strait, so treat this as a live snapshot, not a "
            f"historical average."
        )
        return {
            "places": [{"place_name": display_name, "reasoning": reasoning, "radius_km": radius, "confidence": "high"}],
            "place_name": display_name,
            "reasoning": reasoning,
            "radius_km": radius,
            "confidence": "high",
            "source_urls": [],
            "live_data_source": "AIS (vessel tracking)",
        }

    # ── Aviation (ADS-B) — global count only ──────────────────────────────
    if any(w in q for w in AVIATION_WORDS) and any(k in q for k in COUNT_WORDS):
        stats = aircraft_store.get_stats()
        return {
            "places": [],
            "place_name": None,
            "reasoning": (
                f"Live ADS-B tracking currently shows {stats.get('active_aircraft', 0)} "
                f"aircraft being tracked globally. This is a live snapshot, not a web "
                f"search result — coverage depends on ADS-B receiver density, so this "
                f"reflects tracked aircraft, not literally every aircraft in the air."
            ),
            "radius_km": None,
            "confidence": "high",
            "source_urls": [],
            "live_data_source": "ADS-B (aircraft tracking)",
        }

    # ── USGS earthquakes / NASA FIRMS fires — global recency count ────────
    for source_name, words in (("USGS", EARTHQUAKE_WORDS), ("NASA FIRMS", FIRE_WORDS)):
        if any(w in q for w in words) and any(k in q for k in COUNT_WORDS):
            events = intel_store.query(sources=[source_name], limit=500)
            return {
                "places": [],
                "place_name": None,
                "reasoning": (
                    f"Vayu's live {source_name} feed currently holds {len(events)} event(s) "
                    f"reported globally within roughly the last 24 hours. This is a live "
                    f"intel-feed snapshot, not a web search result, and reflects only what "
                    f"{source_name} has reported recently — not necessarily every such event "
                    f"happening right now."
                ),
                "radius_km": None,
                "confidence": "medium",
                "source_urls": [],
                "live_data_source": f"{source_name} (live feed)",
            }

    return None
