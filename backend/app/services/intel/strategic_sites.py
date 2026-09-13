"""
strategic_sites.py — a curated, hand-maintained list of major global
ports, oil refineries, and mines (public-domain facts: names and
locations of major industrial sites, not licensed data from any
provider), plus a live "recent signals" check against everything else
this project already tracks — nearby earthquakes (USGS), nearby
dark-vessel flags (for ports), regional GDELT tone, AND actual recent
news headlines (title + source + date + link) for that specific site,
not just an aggregate tone number.

This is deliberately a hand-curated list, not a scraped/paid registry
(e.g. the full UN/LOCODE port list or a commercial mining database) —
it's ~64 sites spanning every major region (expanded from an initial
~32 that skewed toward East Asia/Middle East/US — Africa beyond South
Africa, more of Europe, South America, Central Asia, and DR Congo's
cobalt belt were previously missing entirely), kept small enough to
review and extend by hand rather than an unreviewable bulk import.
"""

import math
from typing import Any

SITES = [
    # ── Ports ──
    {"id": "shanghai", "name": "Port of Shanghai", "type": "port", "lat": 31.22, "lon": 121.49, "note": "World's busiest container port"},
    {"id": "singapore", "name": "Port of Singapore", "type": "port", "lat": 1.29, "lon": 103.75, "note": "Key Malacca Strait transshipment hub"},
    {"id": "ningbo_zhoushan", "name": "Port of Ningbo-Zhoushan", "type": "port", "lat": 29.87, "lon": 121.97, "note": "World's busiest port by cargo tonnage"},
    {"id": "shenzhen", "name": "Port of Shenzhen", "type": "port", "lat": 22.48, "lon": 114.07, "note": "Major container port, Pearl River Delta"},
    {"id": "qingdao", "name": "Port of Qingdao", "type": "port", "lat": 36.07, "lon": 120.33, "note": "Major North China container/iron-ore port"},
    {"id": "guangzhou", "name": "Port of Guangzhou", "type": "port", "lat": 23.10, "lon": 113.47, "note": "One of China's largest ports by cargo tonnage"},
    {"id": "tianjin", "name": "Port of Tianjin", "type": "port", "lat": 38.98, "lon": 117.73, "note": "Largest port in northern China, gateway to Beijing"},
    {"id": "rotterdam", "name": "Port of Rotterdam", "type": "port", "lat": 51.95, "lon": 4.14, "note": "Largest port in Europe"},
    {"id": "antwerp", "name": "Port of Antwerp", "type": "port", "lat": 51.29, "lon": 4.34, "note": "Major European chemicals/container hub"},
    {"id": "busan", "name": "Port of Busan", "type": "port", "lat": 35.10, "lon": 129.04, "note": "South Korea's main container port"},
    {"id": "hong_kong", "name": "Port of Hong Kong", "type": "port", "lat": 22.30, "lon": 114.17, "note": "Major transshipment hub"},
    {"id": "jebel_ali", "name": "Jebel Ali Port", "type": "port", "lat": 25.01, "lon": 55.06, "note": "Largest port in the Middle East, near Strait of Hormuz"},
    {"id": "los_angeles", "name": "Port of Los Angeles", "type": "port", "lat": 33.73, "lon": -118.26, "note": "Busiest container port in the Americas"},
    {"id": "long_beach", "name": "Port of Long Beach", "type": "port", "lat": 33.75, "lon": -118.19, "note": "Second-busiest US container port"},
    {"id": "houston", "name": "Port of Houston", "type": "port", "lat": 29.73, "lon": -95.28, "note": "Largest US port by foreign tonnage, oil/gas hub"},
    {"id": "new_york_nj", "name": "Port of New York and New Jersey", "type": "port", "lat": 40.67, "lon": -74.14, "note": "Busiest port on the US East Coast"},
    {"id": "savannah", "name": "Port of Savannah", "type": "port", "lat": 32.08, "lon": -81.10, "note": "Largest single-terminal container facility in North America"},
    {"id": "vancouver", "name": "Port of Vancouver", "type": "port", "lat": 49.29, "lon": -123.11, "note": "Canada's largest and most diversified port"},
    {"id": "fujairah", "name": "Port of Fujairah", "type": "port", "lat": 25.16, "lon": 56.34, "note": "Major bunkering hub outside the Strait of Hormuz"},
    {"id": "santos", "name": "Port of Santos", "type": "port", "lat": -23.96, "lon": -46.30, "note": "Largest port in Latin America"},
    {"id": "hamburg", "name": "Port of Hamburg", "type": "port", "lat": 53.54, "lon": 9.97, "note": "Germany's largest port"},
    {"id": "valencia", "name": "Port of Valencia", "type": "port", "lat": 39.44, "lon": -0.32, "note": "Busiest container port on the Mediterranean's western edge"},
    {"id": "piraeus", "name": "Port of Piraeus", "type": "port", "lat": 37.94, "lon": 23.63, "note": "Largest port in Greece, major China-Europe gateway (COSCO-operated)"},
    {"id": "jawaharlal_nehru", "name": "Jawaharlal Nehru Port (JNPT)", "type": "port", "lat": 18.95, "lon": 72.95, "note": "India's largest container port, near Mumbai"},
    {"id": "colombo", "name": "Port of Colombo", "type": "port", "lat": 6.95, "lon": 79.84, "note": "Key South Asian transshipment hub"},
    {"id": "tanjung_pelepas", "name": "Port of Tanjung Pelepas", "type": "port", "lat": 1.36, "lon": 103.55, "note": "Major Malaysian transshipment port at the Malacca Strait's mouth"},
    {"id": "laem_chabang", "name": "Laem Chabang Port", "type": "port", "lat": 13.08, "lon": 100.89, "note": "Thailand's largest deep-sea port"},
    {"id": "durban", "name": "Port of Durban", "type": "port", "lat": -29.87, "lon": 31.02, "note": "Busiest port in Africa"},
    {"id": "lagos_apapa", "name": "Lagos (Apapa) Port", "type": "port", "lat": 6.45, "lon": 3.36, "note": "Nigeria's principal port, West Africa's busiest"},
    {"id": "tanger_med", "name": "Tanger Med Port", "type": "port", "lat": 35.88, "lon": -5.51, "note": "Largest port in the Mediterranean/Africa, at the Strait of Gibraltar"},
    {"id": "suez_port_said", "name": "Port Said (Suez Canal)", "type": "port", "lat": 31.26, "lon": 32.30, "note": "Northern gateway to the Suez Canal"},

    # ── Oil refineries ──
    {"id": "jamnagar", "name": "Jamnagar Refinery", "type": "refinery", "lat": 22.37, "lon": 69.85, "note": "World's largest single-site oil refinery (Reliance)"},
    {"id": "ras_tanura", "name": "Ras Tanura Refinery", "type": "refinery", "lat": 26.64, "lon": 50.16, "note": "Saudi Aramco's key export terminal/refinery"},
    {"id": "ruwais", "name": "Ruwais Refinery", "type": "refinery", "lat": 24.11, "lon": 52.73, "note": "ADNOC's major UAE refining/petrochemical complex"},
    {"id": "ulsan", "name": "Ulsan Refinery Complex", "type": "refinery", "lat": 35.50, "lon": 129.38, "note": "South Korea's largest refining hub (SK Energy)"},
    {"id": "yeosu", "name": "Yeosu Refinery Complex", "type": "refinery", "lat": 34.76, "lon": 127.66, "note": "Major South Korean refining/petrochemical complex (GS Caltex)"},
    {"id": "baytown", "name": "Baytown Refinery", "type": "refinery", "lat": 29.75, "lon": -95.01, "note": "One of the largest US refineries (ExxonMobil)"},
    {"id": "port_arthur", "name": "Port Arthur Refinery", "type": "refinery", "lat": 29.93, "lon": -93.94, "note": "Largest refinery in North America (Motiva)"},
    {"id": "baton_rouge", "name": "Baton Rouge Refinery", "type": "refinery", "lat": 30.52, "lon": -91.20, "note": "One of the largest US refineries (ExxonMobil), on the Mississippi"},
    {"id": "rotterdam_refinery", "name": "Rotterdam Refinery", "type": "refinery", "lat": 51.90, "lon": 4.35, "note": "Shell's largest European refinery"},
    {"id": "antwerp_refinery", "name": "Antwerp Refinery", "type": "refinery", "lat": 51.28, "lon": 4.34, "note": "One of Europe's largest integrated refining/petrochemical clusters"},
    {"id": "sikka", "name": "Sikka Refinery (Essar)", "type": "refinery", "lat": 22.47, "lon": 69.83, "note": "Major Indian refinery, Gulf of Kutch"},
    {"id": "yanbu", "name": "Yanbu Refinery", "type": "refinery", "lat": 24.09, "lon": 38.06, "note": "Saudi Aramco refinery on the Red Sea coast"},
    {"id": "abadan", "name": "Abadan Refinery", "type": "refinery", "lat": 30.35, "lon": 48.28, "note": "Iran's oldest and one of its largest refineries"},
    {"id": "omsk", "name": "Omsk Refinery", "type": "refinery", "lat": 54.95, "lon": 73.37, "note": "One of Russia's largest oil refineries (Gazprom Neft)"},
    {"id": "singapore_refinery", "name": "Jurong Island Refining Complex", "type": "refinery", "lat": 1.27, "lon": 103.69, "note": "Singapore's major refining/petrochemical hub"},

    # ── Mines ──
    {"id": "escondida", "name": "Escondida Mine", "type": "mine", "lat": -24.27, "lon": -69.07, "note": "World's largest copper mine (Chile)"},
    {"id": "grasberg", "name": "Grasberg Mine", "type": "mine", "lat": -4.05, "lon": 137.12, "note": "World's largest gold mine / major copper mine (Indonesia)"},
    {"id": "carajas", "name": "Carajás Mine", "type": "mine", "lat": -6.05, "lon": -50.16, "note": "World's largest iron ore mine (Brazil)"},
    {"id": "pilbara_newman", "name": "Newman Iron Ore Hub", "type": "mine", "lat": -23.35, "lon": 119.73, "note": "Core of Australia's Pilbara iron ore region"},
    {"id": "bingham_canyon", "name": "Bingham Canyon Mine", "type": "mine", "lat": 40.52, "lon": -112.15, "note": "One of the largest open-pit copper mines (Utah, US)"},
    {"id": "olympic_dam", "name": "Olympic Dam Mine", "type": "mine", "lat": -30.44, "lon": 136.88, "note": "Major copper/uranium/gold/silver mine (Australia)"},
    {"id": "chuquicamata", "name": "Chuquicamata Mine", "type": "mine", "lat": -22.30, "lon": -68.90, "note": "One of the largest open-pit copper mines by excavation (Chile)"},
    {"id": "witwatersrand", "name": "Witwatersrand Basin", "type": "mine", "lat": -26.20, "lon": 27.90, "note": "Historic core of South African gold mining"},
    {"id": "cerro_verde", "name": "Cerro Verde Mine", "type": "mine", "lat": -16.54, "lon": -71.58, "note": "One of Peru's largest copper mines"},
    {"id": "antamina", "name": "Antamina Mine", "type": "mine", "lat": -9.53, "lon": -77.03, "note": "One of the world's largest copper-zinc mines (Peru)"},
    {"id": "kolwezi_katanga", "name": "Katanga Copperbelt (Kolwezi)", "type": "mine", "lat": -10.72, "lon": 25.47, "note": "Core of DR Congo's copper/cobalt mining region"},
    {"id": "mutanda", "name": "Mutanda Mine", "type": "mine", "lat": -10.65, "lon": 25.66, "note": "One of the world's largest cobalt mines (DR Congo)"},
    {"id": "norilsk", "name": "Norilsk Mining Complex", "type": "mine", "lat": 69.35, "lon": 88.20, "note": "World's largest nickel and palladium producer (Russia)"},
    {"id": "oyu_tolgoi", "name": "Oyu Tolgoi Mine", "type": "mine", "lat": 43.01, "lon": 106.85, "note": "One of the world's largest copper-gold mines (Mongolia)"},
    {"id": "muruntau", "name": "Muruntau Mine", "type": "mine", "lat": 41.51, "lon": 64.58, "note": "World's largest open-pit gold mine (Uzbekistan)"},
    {"id": "greenbushes", "name": "Greenbushes Lithium Mine", "type": "mine", "lat": -33.87, "lon": 116.07, "note": "World's largest hard-rock lithium mine (Australia)"},
    {"id": "salar_de_atacama", "name": "Salar de Atacama", "type": "mine", "lat": -23.50, "lon": -68.25, "note": "Major global lithium brine source (Chile)"},
    {"id": "mount_isa", "name": "Mount Isa Mine", "type": "mine", "lat": -20.73, "lon": 139.50, "note": "Major copper/lead/zinc/silver mine (Australia)"},
]

PROXIMITY_KM = 500  # generous — this is "is anything regionally relevant happening", not a precise blast radius
NEWS_PROXIMITY_KM = 150  # tighter than PROXIMITY_KM — an actual headline shown as "news about this site"
                           # should be reasonably local, not "somewhere in the same 500km region"
NEWS_LIMIT = 5


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


def get_recent_news(site: dict, gdelt_events: list[dict], limit: int = NEWS_LIMIT) -> list[dict]:
    """Actual headlines (title/source domain/date/link) from GDELT
    events near this specific site, most recent first — this is the
    'what's actually going on here lately' the tone aggregate in
    get_recent_signals() doesn't show on its own."""
    nearby = []
    for ev in gdelt_events:
        meta = ev.get("meta", {})
        if "lat" not in ev or "lon" not in ev or not ev.get("title"):
            continue
        dist = _haversine_km(site["lat"], site["lon"], ev["lat"], ev["lon"])
        if dist > NEWS_PROXIMITY_KM:
            continue
        nearby.append({
            "title": ev["title"],
            "domain": meta.get("domain", ""),
            "url": meta.get("url", ""),
            "date": ev.get("ts", ""),
            "tone": meta.get("tone"),
            "distance_km": round(dist, 0),
        })
    nearby.sort(key=lambda n: n.get("date", ""), reverse=True)
    return nearby[:limit]


def list_sites_with_signals(usgs_events: list[dict], gdelt_events: list[dict], dark_flags: list[dict], site_type: str | None = None) -> list[dict]:
    out = []
    for site in list_sites(site_type):
        signals = get_recent_signals(site, usgs_events, gdelt_events, dark_flags)
        news = get_recent_news(site, gdelt_events)
        out.append({**site, "signals": signals, "recent_news": news})
    return out
