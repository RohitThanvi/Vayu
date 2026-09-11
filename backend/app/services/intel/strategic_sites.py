"""
strategic_sites.py — a curated, hand-maintained list of major global
ports, oil refineries, and mines (public-domain facts: names and
locations of major industrial sites, not licensed data from any
provider), plus a live "recent signals" check against everything else
this project already tracks — nearby earthquakes (USGS), nearby
dark-vessel flags (for ports), and regional GDELT tone.

This is deliberately a hand-curated list, not a scraped/paid registry
(e.g. the full UN/LOCODE port list or a commercial mining database) —
it's the ~35 sites that actually matter for chokepoint/commodity
context, kept small enough to review and extend by hand rather than
an unreviewable bulk import.
"""

import math
from typing import Any

SITES = [
    # ── Ports ──
    {"id": "shanghai", "name": "Port of Shanghai", "type": "port", "lat": 31.22, "lon": 121.49, "note": "World's busiest container port"},
    {"id": "singapore", "name": "Port of Singapore", "type": "port", "lat": 1.29, "lon": 103.75, "note": "Key Malacca Strait transshipment hub"},
    {"id": "ningbo_zhoushan", "name": "Port of Ningbo-Zhoushan", "type": "port", "lat": 29.87, "lon": 121.97, "note": "World's busiest port by cargo tonnage"},
    {"id": "shenzhen", "name": "Port of Shenzhen", "type": "port", "lat": 22.48, "lon": 114.07, "note": "Major container port, Pearl River Delta"},
    {"id": "rotterdam", "name": "Port of Rotterdam", "type": "port", "lat": 51.95, "lon": 4.14, "note": "Largest port in Europe"},
    {"id": "antwerp", "name": "Port of Antwerp", "type": "port", "lat": 51.29, "lon": 4.34, "note": "Major European chemicals/container hub"},
    {"id": "busan", "name": "Port of Busan", "type": "port", "lat": 35.10, "lon": 129.04, "note": "South Korea's main container port"},
    {"id": "hong_kong", "name": "Port of Hong Kong", "type": "port", "lat": 22.30, "lon": 114.17, "note": "Major transshipment hub"},
    {"id": "jebel_ali", "name": "Jebel Ali Port", "type": "port", "lat": 25.01, "lon": 55.06, "note": "Largest port in the Middle East, near Strait of Hormuz"},
    {"id": "los_angeles", "name": "Port of Los Angeles", "type": "port", "lat": 33.73, "lon": -118.26, "note": "Busiest container port in the Americas"},
    {"id": "long_beach", "name": "Port of Long Beach", "type": "port", "lat": 33.75, "lon": -118.19, "note": "Second-busiest US container port"},
    {"id": "houston", "name": "Port of Houston", "type": "port", "lat": 29.73, "lon": -95.28, "note": "Largest US port by foreign tonnage, oil/gas hub"},
    {"id": "fujairah", "name": "Port of Fujairah", "type": "port", "lat": 25.16, "lon": 56.34, "note": "Major bunkering hub outside the Strait of Hormuz"},
    {"id": "santos", "name": "Port of Santos", "type": "port", "lat": -23.96, "lon": -46.30, "note": "Largest port in Latin America"},
    {"id": "hamburg", "name": "Port of Hamburg", "type": "port", "lat": 53.54, "lon": 9.97, "note": "Germany's largest port"},

    # ── Oil refineries ──
    {"id": "jamnagar", "name": "Jamnagar Refinery", "type": "refinery", "lat": 22.37, "lon": 69.85, "note": "World's largest single-site oil refinery (Reliance)"},
    {"id": "ras_tanura", "name": "Ras Tanura Refinery", "type": "refinery", "lat": 26.64, "lon": 50.16, "note": "Saudi Aramco's key export terminal/refinery"},
    {"id": "ruwais", "name": "Ruwais Refinery", "type": "refinery", "lat": 24.11, "lon": 52.73, "note": "ADNOC's major UAE refining/petrochemical complex"},
    {"id": "ulsan", "name": "Ulsan Refinery Complex", "type": "refinery", "lat": 35.50, "lon": 129.38, "note": "South Korea's largest refining hub (SK Energy)"},
    {"id": "yeosu", "name": "Yeosu Refinery Complex", "type": "refinery", "lat": 34.76, "lon": 127.66, "note": "Major South Korean refining/petrochemical complex (GS Caltex)"},
    {"id": "baytown", "name": "Baytown Refinery", "type": "refinery", "lat": 29.75, "lon": -95.01, "note": "One of the largest US refineries (ExxonMobil)"},
    {"id": "port_arthur", "name": "Port Arthur Refinery", "type": "refinery", "lat": 29.93, "lon": -93.94, "note": "Largest refinery in North America (Motiva)"},
    {"id": "rotterdam_refinery", "name": "Rotterdam Refinery", "type": "refinery", "lat": 51.90, "lon": 4.35, "note": "Shell's largest European refinery"},
    {"id": "sikka", "name": "Sikka Refinery (Essar)", "type": "refinery", "lat": 22.47, "lon": 69.83, "note": "Major Indian refinery, Gulf of Kutch"},

    # ── Mines ──
    {"id": "escondida", "name": "Escondida Mine", "type": "mine", "lat": -24.27, "lon": -69.07, "note": "World's largest copper mine (Chile)"},
    {"id": "grasberg", "name": "Grasberg Mine", "type": "mine", "lat": -4.05, "lon": 137.12, "note": "World's largest gold mine / major copper mine (Indonesia)"},
    {"id": "carajas", "name": "Carajás Mine", "type": "mine", "lat": -6.05, "lon": -50.16, "note": "World's largest iron ore mine (Brazil)"},
    {"id": "pilbara_newman", "name": "Newman Iron Ore Hub", "type": "mine", "lat": -23.35, "lon": 119.73, "note": "Core of Australia's Pilbara iron ore region"},
    {"id": "bingham_canyon", "name": "Bingham Canyon Mine", "type": "mine", "lat": 40.52, "lon": -112.15, "note": "One of the largest open-pit copper mines (Utah, US)"},
    {"id": "olympic_dam", "name": "Olympic Dam Mine", "type": "mine", "lat": -30.44, "lon": 136.88, "note": "Major copper/uranium/gold/silver mine (Australia)"},
    {"id": "chuquicamata", "name": "Chuquicamata Mine", "type": "mine", "lat": -22.30, "lon": -68.90, "note": "One of the largest open-pit copper mines by excavation (Chile)"},
    {"id": "witwatersrand", "name": "Witwatersrand Basin", "type": "mine", "lat": -26.20, "lon": 27.90, "note": "Historic core of South African gold mining"},
]

PROXIMITY_KM = 500  # generous — this is "is anything regionally relevant happening", not a precise blast radius


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def list_sites(site_type: str | None = None) -> list[dict]:
    if site_type:
        return [s for s in SITES if s["type"] == site_type]
    return list(SITES)


def get_recent_signals(site: dict, usgs_events: list[dict], gdelt_events: list[dict], dark_flags: list[dict]) -> list[dict]:
    """Returns a short list of {kind, note} signals near this specific
    site — only the things that are actually near it, not a dump of
    everything tracked globally."""
    signals = []

    for ev in usgs_events:
        mag = ev.get("meta", {}).get("magnitude")
        if mag is None or mag < 5.0 or "lat" not in ev or "lon" not in ev:
            continue
        dist = _haversine_km(site["lat"], site["lon"], ev["lat"], ev["lon"])
        if dist <= PROXIMITY_KM:
            signals.append({"kind": "seismic", "note": f"M{mag:.1f} earthquake {dist:.0f}km away", "severity": "warn" if mag < 6.5 else "critical"})

    if site["type"] == "port":
        for f in dark_flags:
            last = f.get("last_known", {})
            if "lat" not in last or "lon" not in last:
                continue
            dist = _haversine_km(site["lat"], site["lon"], last["lat"], last["lon"])
            if dist <= PROXIMITY_KM:
                signals.append({"kind": "dark_vessel", "note": f"{f['name']} flagged dark-vessel event {dist:.0f}km away", "severity": "warn"})

    nearby_tones = []
    for ev in gdelt_events:
        meta = ev.get("meta", {})
        if "tone" not in meta or "lat" not in ev or "lon" not in ev:
            continue
        dist = _haversine_km(site["lat"], site["lon"], ev["lat"], ev["lon"])
        if dist <= PROXIMITY_KM:
            nearby_tones.append(meta["tone"])
    if nearby_tones:
        avg = sum(nearby_tones) / len(nearby_tones)
        if avg < -3:
            signals.append({"kind": "tone", "note": f"Regional news tone is negative (avg {avg:.1f}, {len(nearby_tones)} articles)", "severity": "warn" if avg > -6 else "critical"})

    return signals


def list_sites_with_signals(usgs_events: list[dict], gdelt_events: list[dict], dark_flags: list[dict], site_type: str | None = None) -> list[dict]:
    out = []
    for site in list_sites(site_type):
        signals = get_recent_signals(site, usgs_events, gdelt_events, dark_flags)
        out.append({**site, "signals": signals})
    return out
