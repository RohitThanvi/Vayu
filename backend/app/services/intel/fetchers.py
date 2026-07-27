"""
Real-time intelligence fetchers.
All sources used here are completely free and require no API keys.

  - USGS Earthquake API  → https://earthquake.usgs.gov/fdsnws/event/1/
  - NASA FIRMS (VIIRS)   → https://firms.modaps.eosdis.nasa.gov/api/
  - ACLED               → https://acleddata.com/api/  (free, needs key)
  - GDELT               → https://api.gdeltproject.org/ (free, no key)

Each fetcher returns a list of IntelEvent dicts ready to broadcast.
"""

import asyncio
import logging
import httpx
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── Shared schema ─────────────────────────────────────────────────────────────
def _event(
    source: str,
    tag: str,
    title: str,
    detail: str,
    lat: float,
    lon: float,
    severity: str = "info",   # info | warn | critical
    meta: dict = None,
) -> dict:
    return {
        "id": f"{source}-{lat:.3f}-{lon:.3f}-{datetime.utcnow().timestamp():.0f}",
        "source": source,
        "tag": tag,
        "title": title,
        "detail": detail,
        "lat": lat,
        "lon": lon,
        "severity": severity,
        "ts": datetime.utcnow().isoformat() + "Z",
        "meta": meta or {},
    }


# ── USGS Earthquake Feed ──────────────────────────────────────────────────────
USGS_URL = (
    "https://earthquake.usgs.gov/fdsnws/event/1/query"
    "?format=geojson"
    "&minmagnitude=3.5"
    "&orderby=time"
    "&limit=50"
)

async def fetch_usgs(client: httpx.AsyncClient, since_minutes: int = 60) -> list[dict]:
    start = (datetime.utcnow() - timedelta(minutes=since_minutes)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    url = USGS_URL + f"&starttime={start}"
    try:
        resp = await client.get(url, timeout=10)
        resp.raise_for_status()
        features = resp.json().get("features", [])
        events = []
        for f in features:
            props = f["properties"]
            coords = f["geometry"]["coordinates"]
            lon, lat, depth = coords[0], coords[1], coords[2]
            mag = props.get("mag", 0) or 0
            place = props.get("place", "Unknown location")
            severity = "critical" if mag >= 6.0 else "warn" if mag >= 5.0 else "info"
            events.append(_event(
                source="USGS",
                tag="SEISMIC",
                title=f"M{mag:.1f} — {place}",
                detail=f"Magnitude {mag:.1f} event at depth {depth:.0f}km. {place}.",
                lat=lat,
                lon=lon,
                severity=severity,
                meta={"magnitude": mag, "depth_km": depth, "place": place,
                      "usgs_id": f["id"], "felt": props.get("felt"),
                      "tsunami": props.get("tsunami", 0)},
            ))
        logger.info(f"USGS: fetched {len(events)} events")
        return events
    except Exception as e:
        logger.error(f"USGS fetch error: {e}")
        return []


# ── NASA FIRMS Active Fire Feed ───────────────────────────────────────────────
# Suomi NPP (S-NPP) VIIRS experienced a sensor anomaly in March 2026 and its
# data is unreliable or absent. We now try sources in priority order:
#   1. NOAA-20 VIIRS C2  — operational, best quality
#   2. NOAA-21 VIIRS C2  — also operational, secondary
#   3. MODIS C6.1         — lower resolution (1km) but robust long-term record
# All are free public CSV files, no API key required.

FIRMS_SOURCES = [
    {
        "id": "NOAA-20",
        "url": (
            "https://firms.modaps.eosdis.nasa.gov/data/active_fire/"
            "noaa-20-viirs-c2/csv/J1_VIIRS_C2_Global_24h.csv"
        ),
    },
    {
        "id": "NOAA-21",
        "url": (
            "https://firms.modaps.eosdis.nasa.gov/data/active_fire/"
            "noaa-21-viirs-c2/csv/J2_VIIRS_C2_Global_24h.csv"
        ),
    },
    {
        "id": "MODIS",
        "url": (
            "https://firms.modaps.eosdis.nasa.gov/data/active_fire/"
            "c6.1/csv/MODIS_C6_1_Global_24h.csv"
        ),
    },
]


def _parse_firms_csv(text: str, source_id: str, max_events: int = 80) -> list[dict]:
    """Parse a FIRMS active-fire CSV and return a list of IntelEvent dicts."""
    lines = text.strip().split("\n")
    if len(lines) < 2:
        return []

    header = [h.strip() for h in lines[0].split(",")]
    raw_rows = lines[1:]
    step = max(1, len(raw_rows) // max_events)
    sampled = raw_rows[::step][:max_events]
    events = []

    for line in sampled:
        try:
            vals = line.split(",")
            row = dict(zip(header, vals))
            lat = float(row.get("latitude", 0))
            lon = float(row.get("longitude", 0))
            frp = float(row.get("frp", 0) or 0)
            confidence = row.get("confidence", "n").strip().lower()
            acq_date = row.get("acq_date", "")
            acq_time = row.get("acq_time", "")

            # MODIS uses numeric confidence (0-100), VIIRS uses h/n/l
            try:
                conf_num = float(confidence)
                conf_label = "HIGH" if conf_num >= 80 else "NOMINAL" if conf_num >= 50 else "LOW"
            except ValueError:
                conf_label = {"h": "HIGH", "n": "NOMINAL", "l": "LOW"}.get(
                    confidence, confidence.upper()
                )

            severity = "critical" if frp > 500 else "warn" if frp > 100 else "info"
            events.append(_event(
                source="NASA FIRMS",
                tag=f"ACTIVE FIRE · {source_id}",
                title=f"Fire hotspot — FRP {frp:.0f} MW",
                detail=(
                    f"Active fire detected ({source_id} VIIRS). "
                    f"Fire Radiative Power: {frp:.0f} MW. "
                    f"Confidence: {conf_label}. Acquired {acq_date} {acq_time}Z."
                ),
                lat=lat,
                lon=lon,
                severity=severity,
                meta={"frp_mw": frp, "confidence": conf_label,
                      "acq_date": acq_date, "acq_time": acq_time,
                      "satellite": source_id},
            ))
        except (ValueError, KeyError, ZeroDivisionError):
            continue
    return events


async def fetch_firms(client: httpx.AsyncClient) -> list[dict]:
    """Try FIRMS sources in priority order, return first successful result."""
    for source in FIRMS_SOURCES:
        try:
            resp = await client.get(source["url"], timeout=25)
            resp.raise_for_status()
            events = _parse_firms_csv(resp.text, source["id"])
            logger.info(
                f"NASA FIRMS: fetched {len(events)} fire hotspots "
                f"from {source['id']}"
            )
            return events
        except Exception as e:
            logger.warning(f"NASA FIRMS {source['id']} failed: {e} — trying next source")

    logger.error("NASA FIRMS: all sources failed")
    return []


# ── GDELT News Event Feed (via raw GKG 15-min files) ─────────────────────────
# The legacy GDELT GEO 2.0 JSON API (api.gdeltproject.org/api/v2/geo/geo) has
# been retired and now returns 404. GDELT still publishes raw GKG (Global
# Knowledge Graph) files every 15 minutes as tab-delimited CSVs inside a zip.
# This is the same data the old API was built on, just one layer lower.
#
# Pipeline: lastupdate.txt -> latest *.gkg.csv.zip URL -> download -> parse
# We only keep rows whose GKG themes match disaster/conflict keywords, and
# only keep the first valid geocoded location per matching row.

GDELT_LASTUPDATE_URL = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"

GDELT_THEME_KEYWORDS = [
    "FLOOD", "FIRE", "EARTHQUAKE", "DISASTER", "CONFLICT", "ARMEDCONFLICT",
    "KILL", "PROTEST", "TERROR", "WOUND", "EVACU", "CYCLONE", "HURRICANE",
    "TSUNAMI", "DROUGHT", "WILDFIRE", "VOLCANO", "DISPLACED", "CRISISLEX",
]

# Module-level cache so we don't re-download/re-parse the same 15-min file
# if our poll interval (10 min) lands inside the same GDELT publish window.
_gdelt_last_url: str = ""

def _gdelt_pick_theme(themes: list[str]) -> str:
    for t in themes:
        upper = t.upper()
        if any(kw in upper for kw in GDELT_THEME_KEYWORDS):
            return t
    return themes[0] if themes else "NEWS_EVENT"

async def fetch_gdelt(client: httpx.AsyncClient) -> list[dict]:
    global _gdelt_last_url
    try:
        manifest = await client.get(GDELT_LASTUPDATE_URL, timeout=15)
        manifest.raise_for_status()
        gkg_url = None
        for line in manifest.text.strip().split("\n"):
            parts = [p for p in line.strip().split(" ") if p]
            if parts and parts[-1].endswith("gkg.csv.zip"):
                gkg_url = parts[-1]
                break
        if not gkg_url:
            logger.warning("GDELT: no gkg.csv.zip found in manifest")
            return []

        if gkg_url == _gdelt_last_url:
            logger.debug("GDELT: file unchanged since last poll, skipping")
            return []
        _gdelt_last_url = gkg_url

        zresp = await client.get(gkg_url, timeout=45)
        zresp.raise_for_status()

        import zipfile, io, csv
        zf = zipfile.ZipFile(io.BytesIO(zresp.content))
        fname = zf.namelist()[0]
        raw = zf.read(fname).decode("utf-8", errors="ignore")

        events = []
        rows_scanned = 0
        MAX_ROWS = 4000     # bound CPU/memory — files can have 10k+ rows
        MAX_EVENTS = 50

        for row in csv.reader(raw.split("\n"), delimiter="\t"):
            rows_scanned += 1
            if rows_scanned > MAX_ROWS or len(events) >= MAX_EVENTS:
                break
            if len(row) < 11:
                continue
            try:
                themes_raw = row[8]      # V2EnhancedThemes
                locations_raw = row[10]  # V2EnhancedLocations
                if not themes_raw or not locations_raw:
                    continue

                themes = [t.split(",")[0] for t in themes_raw.split(";") if t]
                if not any(any(kw in t.upper() for kw in GDELT_THEME_KEYWORDS) for t in themes):
                    continue

                lat = lon = None
                loc_name = ""
                for loc in locations_raw.split(";"):
                    fields = loc.split("#")
                    if len(fields) >= 7:
                        try:
                            lat = float(fields[5])
                            lon = float(fields[6])
                            loc_name = fields[1]
                            break
                        except ValueError:
                            continue
                if lat is None or lon is None:
                    continue

                tone_field = row[15] if len(row) > 15 else ""
                tone = 0.0
                if tone_field:
                    try:
                        tone = float(tone_field.split(",")[0])
                    except ValueError:
                        pass

                domain = row[3] if len(row) > 3 else "unknown"
                url = row[4] if len(row) > 4 else ""
                matched_theme = _gdelt_pick_theme(themes)
                clean_theme = matched_theme.replace("_", " ").replace("CRISISLEX", "").strip().title() or "News Event"

                severity = "critical" if tone < -5 else "warn" if tone < -2 else "info"
                events.append(_event(
                    source="GDELT",
                    tag=f"NEWS · {matched_theme.split('_')[0][:18].upper()}",
                    title=f"{clean_theme} — {loc_name}",
                    detail=f"Coverage from {domain}. Theme: {matched_theme}. Tone: {tone:.2f}.",
                    lat=lat,
                    lon=lon,
                    severity=severity,
                    meta={"theme": matched_theme, "tone": tone, "domain": domain, "url": url},
                ))
            except (ValueError, IndexError):
                continue

        logger.info(f"GDELT: scanned {rows_scanned} GKG rows, matched {len(events)} events")
        return events
    except Exception as e:
        logger.error(f"GDELT fetch error: {e}")
        return []


# ── ACLED Conflict Feed (OAuth) ───────────────────────────────────────────────
# ACLED migrated from simple email+key URL auth to OAuth password-grant
# authentication in 2025. Old-style API keys are no longer issued.
# Register a free account at https://acleddata.com/register and use that
# account\'s email + password here (NOT an "API key" — there isn\'t one anymore).

ACLED_TOKEN_URL = "https://acleddata.com/oauth/token"
ACLED_READ_URL = "https://acleddata.com/api/acled/read"

# Module-level token cache: {"access_token": str, "expires_at": datetime}
_acled_token_cache: dict = {}

async def _get_acled_token(client: httpx.AsyncClient, email: str, password: str) -> str | None:
    global _acled_token_cache
    now = datetime.utcnow()

    cached = _acled_token_cache.get("access_token")
    expires_at = _acled_token_cache.get("expires_at")
    if cached and expires_at and now < expires_at:
        return cached

    try:
        resp = await client.post(
            ACLED_TOKEN_URL,
            data={
                "username": email,
                "password": password,
                "grant_type": "password",
                "client_id": "acled",
                "scope": "authenticated",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("access_token")
        expires_in = data.get("expires_in", 86400)
        if token:
            _acled_token_cache = {
                "access_token": token,
                "expires_at": now + timedelta(seconds=expires_in - 120),  # refresh 2min early
            }
            logger.info("ACLED: OAuth token acquired")
        return token
    except Exception as e:
        logger.error(f"ACLED OAuth error: {e}")
        return None


async def fetch_acled(
    client: httpx.AsyncClient,
    email: str,
    password: str,
    days_back: int = 7,
) -> list[dict]:
    if not email or not password:
        logger.info("ACLED: no credentials configured, skipping")
        return []

    token = await _get_acled_token(client, email, password)
    if not token:
        return []

    since = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    until = datetime.utcnow().strftime("%Y-%m-%d")
    headers = {"Authorization": f"Bearer {token}"}

    full_params = {
        "event_date": since,
        "event_date_where": "BETWEEN",
        "event_date_end": until,
        "fields": "event_date|event_type|sub_event_type|actor1|location|latitude|longitude|fatalities|notes",
        "limit": 100,
    }

    rows = None
    try:
        resp = await client.get(ACLED_READ_URL, params=full_params, headers=headers, timeout=15)
        resp.raise_for_status()
        rows = resp.json().get("data", [])
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            # "Open"/public-tier myACLED accounts (common with personal Gmail
            # sign-ups) can be denied the full disaggregated field set even
            # though the OAuth token itself is valid. Retry with a bare
            # request (no `fields` restriction) to see if base read access
            # works at all — this tells us whether it's a fields-permission
            # issue vs a full account block.
            logger.warning(
                "ACLED: 403 on full-field request — likely an Open/public "
                "myACLED access-tier restriction (common for personal email "
                "sign-ups). Retrying with a minimal field set..."
            )
            try:
                bare_params = {
                    "event_date": since,
                    "event_date_where": "BETWEEN",
                    "event_date_end": until,
                    "limit": 100,
                }
                resp2 = await client.get(ACLED_READ_URL, params=bare_params, headers=headers, timeout=15)
                resp2.raise_for_status()
                rows = resp2.json().get("data", [])
                logger.info("ACLED: bare request succeeded — your account's access "
                            "tier restricts some fields, not all data.")
            except httpx.HTTPStatusError:
                logger.error(
                    "ACLED: 403 persists even on a bare request. Your myACLED "
                    "account is likely on the 'Open' public tier, which restricts "
                    "API read access entirely until ACLED's Access Team reviews "
                    "your account (this can take time after sign-up). Registering "
                    "with an organizational email instead of a personal Gmail "
                    "address typically gets faster/broader access. Skipping ACLED "
                    "this cycle — other intel sources are unaffected."
                )
                return []
        else:
            logger.error(f"ACLED fetch error: {e}")
            return []
    except Exception as e:
        logger.error(f"ACLED fetch error: {e}")
        return []

    if rows is None:
        return []

    events = []
    for row in rows:
        try:
            lat = float(row.get("latitude", 0))
            lon = float(row.get("longitude", 0))
            fatalities = int(row.get("fatalities", 0) or 0)
            event_type = row.get("event_type", "Unknown")
            location = row.get("location", "")
            actor = row.get("actor1", "")
            notes = (row.get("notes") or "")[:200]
            date = row.get("event_date", "")

            severity = "critical" if fatalities > 10 else "warn" if fatalities > 0 else "info"
            events.append(_event(
                source="ACLED",
                tag=f"CONFLICT · {event_type.upper()}",
                title=f"{event_type} — {location}",
                detail=f"{actor}. {notes} Fatalities: {fatalities}. Date: {date}.",
                lat=lat,
                lon=lon,
                severity=severity,
                meta={"fatalities": fatalities, "event_type": event_type,
                      "actor": actor, "location": location, "date": date},
            ))
        except (ValueError, KeyError, TypeError):
            continue
    logger.info(f"ACLED: fetched {len(events)} conflict events")
    return events


# ── Unified fetch runner ──────────────────────────────────────────────────────
async def fetch_all_intel(
    acled_email: str = "",
    acled_password: str = "",
    since_minutes: int = 60,
) -> list[dict]:
    """Fetch all enabled intelligence sources concurrently."""
    async with httpx.AsyncClient(
        headers={"User-Agent": "VAYU-Intelligence-Terminal/2.0"},
        follow_redirects=True,
    ) as client:
        results = await asyncio.gather(
            fetch_usgs(client, since_minutes),
            fetch_firms(client),
            fetch_gdelt(client),
            fetch_acled(client, acled_email, acled_password),
            return_exceptions=True,
        )

    events = []
    for r in results:
        if isinstance(r, list):
            events.extend(r)
        elif isinstance(r, Exception):
            logger.error(f"Fetcher exception: {r}")

    # Sort newest first (by ts field)
    events.sort(key=lambda e: e["ts"], reverse=True)
    logger.info(f"Intel fetch complete: {len(events)} total events")
    return events
