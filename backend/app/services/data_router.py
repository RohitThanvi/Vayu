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
store.py, dark_vessels.py, edgar_exposure.py, sanctions.py, geo_tone.py,
seismic_disruption.py, macro.py, business_risk.py) — this module
doesn't add a new integration, it just routes a qualifying question to
data Vayu already has instead of a search engine. "How many ships are
crossing the Strait of Hormuz right now" gets answered from actual
live AIS positions, not a guess grounded in old news articles about
the strait. Same idea now extends to "which companies are exposed to
Hormuz" (EDGAR), "are there any dark vessels" (AIS-gap detection),
"any sanctioned vessels tracked" (OFAC), regional GDELT tone, FRED/
World Bank macro figures, and the unified business risk score.

Now async (was sync) since several of the newer checks — EDGAR,
sanctions, macro, business risk — make a live network/DB call rather
than reading an already-in-memory store.

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
from .intel import dark_vessels, edgar_exposure, sanctions, geo_tone as geo_tone_mod
from .intel import seismic_disruption, macro as macro_mod, business_risk

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
EDGAR_WORDS       = ["sec filing", "sec filings", "8-k", "10-k", "10-q", "exposed to", "exposure to", "companies exposed", "public companies", "disclosed"]
DARK_VESSEL_WORDS = ["dark vessel", "dark vessels", "ais gap", "ais gaps", "going dark", "gone dark", "went dark", "transponder off", "transponder disabled"]
SANCTIONS_WORDS   = ["sanctioned vessel", "sanctioned vessels", "ofac", "sdn list", "sanctions list", "under sanctions"]
TONE_WORDS        = ["geopolitical tone", "geopolitical sentiment", "news sentiment", "news tone", "regional tone", "gdelt"]
MACRO_WORDS       = ["interest rate", "interest rates", "fed funds", "federal funds rate", "inflation", "cpi", "treasury yield", "10-year yield", "gdp growth", "macro"]
RISK_SCORE_WORDS  = ["risk score", "how risky", "business risk"]


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


async def try_answer_from_live_data(question: str) -> Optional[Dict[str, Any]]:
    q = question.lower()
    choke_key_hint = _match_chokepoint(q)  # computed once, reused by several blocks below

    # ── EDGAR exposure — which public companies disclosed exposure to a chokepoint ──
    if any(w in q for w in EDGAR_WORDS) and choke_key_hint:
        data = await edgar_exposure.exposure_for_chokepoint(choke_key_hint)
        display_name = CHOKEPOINT_DISPLAY[choke_key_hint]
        filings = data["filings"]
        if filings:
            names = ", ".join(f"{f['company']} ({f['form_type']}, {f['filed']})" for f in filings[:6])
            reasoning = (
                f"SEC EDGAR full-text search found {data['count']} recent 8-K/10-K/10-Q filing(s) "
                f"mentioning {display_name} in the last {data['days_back']} days: {names}. "
                f"This is a live search of actual SEC filings, not a web search summary."
            )
        else:
            reasoning = (
                f"No 8-K/10-K/10-Q filings mentioning {display_name} were found on SEC EDGAR "
                f"in the last {data['days_back']} days."
            )
        return {
            "places": [{"place_name": display_name, "reasoning": reasoning, "radius_km": None, "confidence": "high"}],
            "place_name": display_name, "reasoning": reasoning, "radius_km": None, "confidence": "high",
            "source_urls": [], "live_data_source": "SEC EDGAR full-text search",
        }

    # ── Dark vessels / AIS gaps ─────────────────────────────────────────────
    if any(w in q for w in DARK_VESSEL_WORDS):
        flags = dark_vessels.list_flags(limit=10)
        if flags:
            bits = "; ".join(f["note"] for f in flags[:5])
            reasoning = f"Vayu's own AIS-gap detection currently has {len(flags)} flagged dark-vessel event(s): {bits}"
        else:
            reasoning = (
                "No dark-vessel (AIS gap) events are currently flagged. This checks vessels that "
                "went dark near a monitored chokepoint and reappeared further away than plausible "
                "transit would explain — see Vayu's Maritime tab for ongoing monitoring."
            )
        return {
            "places": [], "place_name": None, "reasoning": reasoning, "radius_km": None,
            "confidence": "high", "source_urls": [], "live_data_source": "Vayu AIS-gap detection",
        }

    # ── Sanctions screening ──────────────────────────────────────────────────
    if any(w in q for w in SANCTIONS_WORDS):
        hits = await sanctions.screen_vessels(vessel_store.query(limit=5000))
        status = sanctions.get_list_status()
        if not status["loaded"]:
            reasoning = "The OFAC sanctions list hasn't loaded yet — try again shortly."
        elif hits:
            names = ", ".join(f"{h['name']} (MMSI {h['mmsi']})" for h in hits[:6])
            reasoning = (
                f"{len(hits)} currently tracked vessel(s) name-match the OFAC SDN list: {names}. "
                f"This is a NAME match only, not confirmed by IMO number — treat as a lead to verify, not a confirmed hit."
            )
        else:
            reasoning = f"None of the currently tracked vessels match the OFAC SDN list ({status['vessel_entries']} vessel entries checked)."
        return {
            "places": [], "place_name": None, "reasoning": reasoning, "radius_km": None,
            "confidence": "medium" if hits else "high", "source_urls": [], "live_data_source": "OFAC SDN list",
        }

    # ── Geopolitical tone (GDELT, aggregated) ───────────────────────────────
    if any(w in q for w in TONE_WORDS):
        events = intel_store.query(sources=["GDELT"], limit=1000)
        regions = geo_tone_mod.compute_tone_regions(events)
        if regions:
            worst = regions[:3]
            bits = "; ".join(f"({r['lat']:.0f}, {r['lon']:.0f}) tone {r['avg_tone']} — {r['label']}, {r['event_count']} events" for r in worst)
            reasoning = f"Aggregated GDELT tone by region (most negative first): {bits}"
        else:
            reasoning = "Not enough recent GDELT events cached yet to compute a regional tone breakdown."
        return {
            "places": [], "place_name": None, "reasoning": reasoning, "radius_km": None,
            "confidence": "medium", "source_urls": [], "live_data_source": "GDELT (aggregated)",
        }

    # ── Macro context (FRED + World Bank, including India) ─────────────────
    if any(w in q for w in MACRO_WORDS):
        snap = await macro_mod.get_macro_snapshot()
        bits = []
        for label, v in snap.get("us", {}).items():
            bits.append(f"US {label.replace('_', ' ')}: {v['value']} (as of {v['date']})")
        for label, v in snap.get("global", {}).items():
            bits.append(f"Global {label.replace('_', ' ')}: {v['value']} (as of {v.get('date')})")
        for label, v in snap.get("india", {}).items():
            bits.append(f"India {label.replace('_', ' ')}: {v['value']} (as of {v.get('date')})")
        reasoning = "; ".join(bits) if bits else "Macro data isn't available right now (FRED_API_KEY may not be configured)."
        return {
            "places": [], "place_name": None, "reasoning": reasoning, "radius_km": None,
            "confidence": "high" if bits else "low", "source_urls": [], "live_data_source": "FRED / World Bank",
        }

    # ── Unified business risk score for a named chokepoint ──────────────────
    if any(w in q for w in RISK_SCORE_WORDS) and choke_key_hint:
        bbox_pair = CHOKEPOINTS[choke_key_hint]
        (min_lat, min_lon), (max_lat, max_lon) = bbox_pair
        vessels_in_area = vessel_store.query(bbox=(min_lat, min_lon, max_lat, max_lon))
        events_gdelt = intel_store.query(sources=["GDELT"], limit=1000)
        events_usgs = intel_store.query(sources=["USGS"], limit=500)
        result = await business_risk.score_chokepoint(choke_key_hint, len(vessels_in_area), events_gdelt, events_usgs, vessels_in_area)
        display_name = CHOKEPOINT_DISPLAY[choke_key_hint]
        reasoning = (
            f"{display_name} business risk score: {result['score']}/100 ({result['band']}). "
            f"Breakdown — traffic anomaly {result['components']['traffic']['score']}, "
            f"regional tone {result['components']['tone']['score']}, "
            f"seismic {result['components']['seismic']['score']}, "
            f"sanctions {result['components']['sanctions']['score']}."
        )
        return {
            "places": [{"place_name": display_name, "reasoning": reasoning, "radius_km": None, "confidence": "high"}],
            "place_name": display_name, "reasoning": reasoning, "radius_km": None, "confidence": "high",
            "source_urls": [], "live_data_source": "Vayu business risk score",
        }

    # ── Maritime (AIS) — only for the 7 named chokepoints already monitored ──
    if any(w in q for w in MARITIME_WORDS):
        choke_key = choke_key_hint
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

    # ── Earthquake near a specific chokepoint (more specific than the ────────
    # generic global USGS count below — checked first when both apply) ──────
    if any(w in q for w in EARTHQUAKE_WORDS) and choke_key_hint:
        events = intel_store.query(sources=["USGS"], limit=500)
        disruptions = [d for d in seismic_disruption.find_disruptions(events, min_magnitude=4.5) if d["chokepoint"] == choke_key_hint]
        display_name = CHOKEPOINT_DISPLAY[choke_key_hint]
        if disruptions:
            bits = "; ".join(d["note"] for d in disruptions[:3])
            reasoning = bits
        else:
            reasoning = f"No magnitude 4.5+ earthquakes within {seismic_disruption.PROXIMITY_KM}km of {display_name} in the recent USGS feed."
        return {
            "places": [{"place_name": display_name, "reasoning": reasoning, "radius_km": None, "confidence": "medium"}],
            "place_name": display_name, "reasoning": reasoning, "radius_km": None, "confidence": "medium",
            "source_urls": [], "live_data_source": "USGS (chokepoint-proximity)",
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
