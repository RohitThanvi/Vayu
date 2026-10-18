"""hazards.py — live natural-hazard alerts (cyclones, floods, earthquakes, volcanoes, tsunami messages) and their market context.

Sources (both free and keyless):
  * GDACS — the UN/EU Global Disaster Alert and Coordination System: https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH
    Orange and Red alerts for earthquakes (EQ), tropical cyclones (TC), floods (FL) and volcanoes (VO). Behaviours that matter and that
    the API documents/exhibits: alert levels must be requested ONE AT A TIME (a combined request silently drops Red/Orange), pages hold
    at most 100 events, an empty body marks the end, and GDACS has no separate tsunami type.
  * NOAA tsunami.gov Atom feeds from the Pacific (PHEBAtom.xml) and National (PAAQAtom.xml) Tsunami Warning Centers: the latest message
    from each centre. Severity here is derived ONLY from the message title; the linked official bulletin governs.
Market context is deliberately simple and labelled as Vayu's own mapping: chokepoints within a radius of the event (reusing the existing
chokepoint/commodity/bellwether tables) and the home index of the affected country, for the few countries whose index Vayu carries.
Nothing here forecasts a market move.
"""
import asyncio
import datetime as dt
import logging
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import httpx

from .commodity_prices import _HEADERS
from .chokepoint_market_correlation import CHOKEPOINT_BELLWETHERS
from .fetchers import _event
from .seismic_disruption import _haversine_km
from .supply_chain import CHOKEPOINT_COMMODITIES, CHOKEPOINT_DISPLAY
from .vessel_store import CHOKEPOINTS

logger = logging.getLogger(__name__)

GDACS_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
TSUNAMI_FEEDS = {"PTWC": "https://www.tsunami.gov/events/xml/PHEBAtom.xml", "NTWC": "https://www.tsunami.gov/events/xml/PAAQAtom.xml"}
SOURCE_GDACS, SOURCE_TSUNAMI = "GDACS", "NOAA Tsunami"
PAGE_SIZE = 100
MAX_PAGES = 5
LIVE_WINDOW_DAYS = 10
LIVE_LEVELS = ("Red", "Orange")
EXPOSURE_RADIUS_KM = 400

TYPES: Dict[str, Dict[str, str]] = {
    "EQ": {"id": "earthquake", "label": "Earthquake", "tag": "SEISMIC"},
    "TC": {"id": "cyclone", "label": "Tropical cyclone", "tag": "CYCLONE"},
    "FL": {"id": "flood", "label": "Flood", "tag": "FLOOD"},
    "VO": {"id": "volcano", "label": "Volcano", "tag": "VOLCANO"},
}
SEVERITY = {"red": "critical", "orange": "warn", "green": "info"}
# Countries whose home index Vayu already carries (market_indices.INDICES). Vayu's own mapping, not GDACS's.
COUNTRY_INDEX = {"IND": ("^NSEI", "Nifty 50"), "USA": ("^GSPC", "S&P 500"), "GBR": ("^FTSE", "FTSE 100"), "JPN": ("^N225", "Nikkei 225"), "CHN": ("000001.SS", "Shanghai Composite")}
# A short, practical list for the disaster event study (ISO 3166-1 alpha-3). Any GDACS country code works in the API; these are common ones.
STUDY_COUNTRIES = {"IND": "India", "USA": "United States", "JPN": "Japan", "CHN": "China", "TWN": "Taiwan", "THA": "Thailand", "PHL": "Philippines",
                   "IDN": "Indonesia", "BGD": "Bangladesh", "PAK": "Pakistan", "VNM": "Vietnam", "KOR": "South Korea", "MEX": "Mexico", "ITA": "Italy",
                   "DEU": "Germany", "FRA": "France", "ESP": "Spain", "BRA": "Brazil", "AUS": "Australia", "TUR": "Turkey", "CHL": "Chile"}


# ── GDACS ────────────────────────────────────────────────────────────────────
def _parse_dt(s: Any) -> Optional[dt.datetime]:
    if not isinstance(s, str) or len(s) < 10:
        return None
    try:
        return dt.datetime.fromisoformat(s[:19]) if len(s) >= 19 else dt.datetime.fromisoformat(s[:10])
    except ValueError:
        return None


def _centre(geom: Dict[str, Any]) -> Optional[tuple]:
    try:
        if geom.get("type") == "Point":
            lon, lat = geom["coordinates"][:2]
        else:                                               # polygon: average of the outer ring's vertices
            ring = geom["coordinates"][0]
            ring = ring[0] if ring and isinstance(ring[0][0], (list, tuple)) else ring
            lon = sum(p[0] for p in ring) / len(ring); lat = sum(p[1] for p in ring) / len(ring)
        lat, lon = float(lat), float(lon)
        return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None
    except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError):
        return None


def parse_gdacs_feature(f: Dict[str, Any], requested_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
    p = (f or {}).get("properties") or {}
    etype = str(p.get("eventtype") or requested_type or "").upper()
    if etype not in TYPES:
        return None
    try:
        eid = int(p["eventid"])
    except (KeyError, TypeError, ValueError):
        return None
    pos = _centre((f or {}).get("geometry") or {})
    sev = p.get("severitydata") or {}
    alert = str(p.get("alertlevel") or "").lower()
    urls = p.get("url") if isinstance(p.get("url"), dict) else {}
    iso = [c for c in re.split(r"[,;\s]+", str(p.get("iso3") or "")) if re.fullmatch(r"[A-Z]{3}", c)]
    return {"type": etype, "kind": TYPES[etype]["id"], "event_id": eid, "name": str(p.get("eventname") or p.get("name") or TYPES[etype]["label"])[:120],
            "alert": alert if alert in SEVERITY else "green", "lat": pos[0] if pos else None, "lon": pos[1] if pos else None,
            "country": str(p.get("country") or "")[:80] or None, "iso3": iso, "from": _parse_dt(p.get("fromdate")), "to": _parse_dt(p.get("todate")),
            "current": str(p.get("iscurrent")).lower() == "true", "severity": sev.get("severity"), "severity_text": (sev.get("severitytext") or None) and str(sev.get("severitytext"))[:120],
            "severity_unit": sev.get("severityunit"), "source": str(p.get("source") or "")[:40] or None,
            "url": urls.get("report") if isinstance(urls.get("report"), str) and urls["report"].startswith("https://") else
                   f"https://www.gdacs.org/report.aspx?eventtype={etype}&eventid={eid}"}


async def _gdacs_page(client: httpx.AsyncClient, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    resp = await client.get(GDACS_URL, params=params, timeout=25)
    resp.raise_for_status()
    if not resp.text.strip():
        return []                                           # empty body = past the last page
    return (resp.json() or {}).get("features") or []


async def fetch_gdacs_parsed(client: httpx.AsyncClient, types: List[str], levels: List[str], start: dt.date, end: dt.date, iso3: Optional[str] = None,
                             max_pages: int = MAX_PAGES) -> List[Dict[str, Any]]:
    """Events of the given types and alert levels in [start, end]. Levels are queried separately and de-duplicated by event id."""
    seen: Dict[tuple, Dict[str, Any]] = {}
    for level in levels:
        for page in range(1, max_pages + 1):
            feats = await _gdacs_page(client, {"eventlist": ";".join(types), "alertlevel": level, "fromDate": start.isoformat(), "toDate": end.isoformat(),
                                               "pageSize": PAGE_SIZE, "pageNumber": page})
            for f in feats:
                ev = parse_gdacs_feature(f, types[0] if len(types) == 1 else None)
                if not ev or (iso3 and iso3 not in ev["iso3"]):
                    continue
                seen.setdefault((ev["type"], ev["event_id"]), ev)
            if len(feats) < PAGE_SIZE:
                break
    return sorted(seen.values(), key=lambda e: e["from"] or dt.datetime.min)


def gdacs_to_intel_event(ev: Dict[str, Any], now: Optional[dt.datetime] = None) -> Optional[Dict[str, Any]]:
    if ev["lat"] is None:
        return None
    now = now or dt.datetime.utcnow()
    meta = TYPES[ev["type"]]
    start = ev["from"]
    ts = start.isoformat() + "Z" if start and (now - start) <= dt.timedelta(hours=24) else None      # older start: stamp "now" so the feed's 24 h window keeps a still-live event
    sev = f" {ev['severity_text']}." if ev["severity_text"] else ""
    where = f" {ev['country']}." if ev["country"] else ""
    return _event(source=SOURCE_GDACS, tag=meta["tag"], title=f"{meta['label']} — {ev['name']} ({ev['alert'].title()} alert)",
                  detail=f"GDACS {ev['alert'].title()} alert.{sev}{where} Alerts are GDACS's own assessment.",
                  lat=ev["lat"], lon=ev["lon"], severity=SEVERITY[ev["alert"]], ts=ts, uid=f"GDACS:{ev['type']}:{ev['event_id']}:{ev['alert']}", dedup="uid",
                  meta={"hazard": ev["kind"], "alert": ev["alert"], "iso3": ev["iso3"], "country": ev["country"], "severity_value": ev["severity"], "severity_text": ev["severity_text"],
                        "severity_unit": ev["severity_unit"], "current": ev["current"], "from": ev["from"].isoformat() if ev["from"] else None, "url": ev["url"], "event_id": ev["event_id"]})


async def fetch_gdacs_events(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    """Current Orange/Red GDACS alerts, as intel-feed events. Never raises (a failed poll returns [])."""
    try:
        end = dt.date.today()
        parsed = await fetch_gdacs_parsed(client, list(TYPES), list(LIVE_LEVELS), end - dt.timedelta(days=LIVE_WINDOW_DAYS), end)
        events = [e for e in (gdacs_to_intel_event(p) for p in parsed if p["current"] or (p["to"] and (dt.datetime.utcnow() - p["to"]).days <= 2)) if e]
        logger.info(f"GDACS: {len(events)} current Orange/Red alerts")
        return events
    except Exception as e:
        logger.error(f"GDACS fetch error: {type(e).__name__}: {e}")
        return []


# ── NOAA tsunami.gov ─────────────────────────────────────────────────────────
_NS = {"a": "http://www.w3.org/2005/Atom", "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#"}


def classify_tsunami(title: str) -> str:
    t = (title or "").upper()
    if "WARNING" in t:
        return "critical"
    if "WATCH" in t or "ADVISORY" in t:
        return "warn"
    return "info"


def parse_tsunami_atom(xml_text: str, centre: str) -> List[Dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    feed_title = (root.findtext("a:title", default="", namespaces=_NS) or "").strip()
    feed_updated = (root.findtext("a:updated", default="", namespaces=_NS) or "").strip()
    out = []
    for e in root.findall("a:entry", _NS):
        try:
            lat = float((e.findtext("geo:lat", default="", namespaces=_NS) or "").strip()); lon = float((e.findtext("geo:long", default="", namespaces=_NS) or "").strip())
        except ValueError:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        bulletin = next((l.get("href") for l in e.findall("a:link", _NS) if l.get("rel") == "alternate" and (l.get("href") or "").startswith("https://")), None)
        out.append({"centre": centre, "message": " ".join(feed_title.split()), "location": " ".join((e.findtext("a:title", default="", namespaces=_NS) or "").split())[:120],
                    "updated": (e.findtext("a:updated", default="", namespaces=_NS) or feed_updated).strip(), "lat": lat, "lon": lon, "bulletin": bulletin,
                    "id": (e.findtext("a:id", default="", namespaces=_NS) or "").strip()})
    return out


def tsunami_to_intel_event(m: Dict[str, Any]) -> Dict[str, Any]:
    sev = classify_tsunami(m["message"])
    return _event(source=SOURCE_TSUNAMI, tag="TSUNAMI", title=f"{m['message'].title()} — {m['location'] or 'see bulletin'}",
                  detail=f"NOAA {m['centre']} message. The linked official bulletin is authoritative; severity shown is derived from the message type only.",
                  lat=m["lat"], lon=m["lon"], severity=sev, ts=m["updated"] if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", m["updated"] or "") else None,
                  uid=f"NOAA-TSUNAMI:{m['centre']}:{m['id'] or m['updated']}", dedup="uid",
                  meta={"hazard": "tsunami", "centre": m["centre"], "message": m["message"], "url": m["bulletin"], "location": m["location"]})


async def fetch_tsunami_events(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for centre, url in TSUNAMI_FEEDS.items():
        try:
            resp = await client.get(url, timeout=15)
            resp.raise_for_status()
            events += [tsunami_to_intel_event(m) for m in parse_tsunami_atom(resp.text, centre)]
        except Exception as e:
            logger.warning(f"tsunami.gov {centre} feed failed: {type(e).__name__}")
    return events


# ── market context ───────────────────────────────────────────────────────────
def exposure_for(lat: Optional[float], lon: Optional[float], iso3: Optional[List[str]] = None, radius_km: int = EXPOSURE_RADIUS_KM) -> Dict[str, Any]:
    chokes = []
    if lat is not None and lon is not None:
        for cp, ((la1, lo1), (la2, lo2)) in CHOKEPOINTS.items():
            d = _haversine_km(lat, lon, (la1 + la2) / 2, (lo1 + lo2) / 2)
            if d <= radius_km:
                chokes.append({"chokepoint": cp, "display": CHOKEPOINT_DISPLAY.get(cp, cp), "distance_km": round(d), "watch_commodities": CHOKEPOINT_COMMODITIES.get(cp, []),
                               "bellwethers": list(CHOKEPOINT_BELLWETHERS.get(cp, []))})
    chokes.sort(key=lambda c: c["distance_km"])
    idx = [{"iso3": c, "symbol": COUNTRY_INDEX[c][0], "label": COUNTRY_INDEX[c][1]} for c in (iso3 or []) if c in COUNTRY_INDEX]
    return {"chokepoints": chokes, "country_indices": idx, "radius_km": radius_km,
            "basis": "Vayu's own proximity/country mapping — context for what to watch, not a forecast of any market move."}


# ── history, for event studies ───────────────────────────────────────────────
async def fetch_history_dates(type_code: str, iso3: str, levels: List[str], years: int = 5) -> List[Dict[str, str]]:
    """One event per start date (strongest wording kept) for a hazard type in a country over the last `years`, from GDACS."""
    if type_code not in TYPES:
        raise ValueError("Unknown hazard type.")
    if not re.fullmatch(r"[A-Z]{3}", iso3 or ""):
        raise ValueError("Country must be a 3-letter ISO code such as IND.")
    for l in levels:
        if l not in ("Red", "Orange", "Green"):
            raise ValueError("Unknown alert level.")
    end = dt.date.today()
    async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True) as client:
        parsed = await fetch_gdacs_parsed(client, [type_code], levels, end - dt.timedelta(days=years * 365 - 5), end, iso3=iso3)
    by_day: Dict[str, Dict[str, str]] = {}
    for p in parsed:
        if not p["from"]:
            continue
        day = p["from"].date().isoformat()
        by_day.setdefault(day, {"date": day, "label": f"{TYPES[type_code]['label']} {p['name']} ({p['alert'].title()})"[:60]})
    return [by_day[d] for d in sorted(by_day)]
