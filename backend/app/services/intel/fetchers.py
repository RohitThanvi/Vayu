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
            logger.info("GDELT: file unchanged since last poll (GDELT publishes every ~15 min, we poll every 10), skipping")
            return []

        # NOTE: _gdelt_last_url is intentionally NOT updated here yet — see
        # below. Updating it before confirming the fetch succeeded was a
        # real bug: if the zip 404s (GDELT's manifest can point to a
        # filename slightly before that file finishes publishing to their
        # CDN — a known, external timing lag, not something Vayu controls),
        # the file would already be marked "seen", and the NEXT poll could
        # see the same still-current manifest entry and skip it as
        # "unchanged" — permanently losing that whole 15-minute window's
        # events instead of just delaying by one cycle.
        zresp = None
        for attempt in (1, 2):
            try:
                zresp = await client.get(gkg_url, timeout=45)
                zresp.raise_for_status()
                break
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404 and attempt == 1:
                    # Likely GDELT's own manifest-vs-CDN publish lag (typically
                    # resolves within well under a minute) rather than a real
                    # problem — one short retry usually recovers within the
                    # same poll instead of waiting the full next 10-minute cycle.
                    logger.info(f"GDELT: {gkg_url} not yet available (likely publish lag), retrying once in 20s")
                    await asyncio.sleep(20)
                    continue
                raise
        if zresp is None:
            return []

        _gdelt_last_url = gkg_url  # only mark as seen once the fetch actually succeeded

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

                # Both fields include a character offset into the source
                # article ("ThemeName,CharOffset;..." and
                # "...#Lat#Lon#FeatureID#CharOffset;...") — previously
                # discarded (themes: t.split(",")[0] dropped it; locations:
                # parsing stopped at field 6/lon). Without it, the code
                # picked "the first disaster-keyword theme" and "the first
                # parseable location" INDEPENDENTLY from the article's full
                # theme/location lists — since one GKG row is a WHOLE
                # article that can mention many unrelated places and
                # themes throughout its text, this could pair a theme from
                # one part of the article with a location mentioned
                # somewhere else entirely unrelated (confirmed real-world
                # case: a story's disaster theme paired with a passing,
                # unrelated place-name mention elsewhere in the same
                # article — headline and map location end up describing
                # different things, and the attached URL is for the whole
                # article, not specifically either one). Using the offsets
                # to pick the location mentioned NEAREST to the matched
                # theme's position in the text is a standard GDELT
                # correlation heuristic and meaningfully reduces (though,
                # since it's proximity not guaranteed co-reference, doesn't
                # perfectly eliminate) this class of mismatch.
                theme_candidates = []  # (name, char_offset or None)
                for t in themes_raw.split(";"):
                    if not t:
                        continue
                    parts = t.split(",")
                    name = parts[0]
                    offset = None
                    if len(parts) > 1:
                        try:
                            offset = int(parts[1])
                        except ValueError:
                            pass
                    theme_candidates.append((name, offset))

                themes = [name for name, _ in theme_candidates]
                if not any(any(kw in t.upper() for kw in GDELT_THEME_KEYWORDS) for t in themes):
                    continue

                loc_candidates = []  # (name, lat, lon, char_offset or None)
                for loc in locations_raw.split(";"):
                    fields = loc.split("#")
                    if len(fields) >= 7:
                        try:
                            lat_c = float(fields[5])
                            lon_c = float(fields[6])
                        except ValueError:
                            continue
                        offset_c = None
                        if len(fields) >= 8:
                            try:
                                offset_c = int(fields[7])
                            except ValueError:
                                pass
                        loc_candidates.append((fields[1], lat_c, lon_c, offset_c))
                if not loc_candidates:
                    continue

                # Prefer the first disaster-keyword theme that has a usable
                # offset (needed for proximity matching); fall back to the
                # plain first-match if none of them do.
                matched_theme, theme_offset = None, None
                for name, offset in theme_candidates:
                    if any(kw in name.upper() for kw in GDELT_THEME_KEYWORDS):
                        matched_theme, theme_offset = name, offset
                        if offset is not None:
                            break

                if theme_offset is not None and any(o is not None for _, _, _, o in loc_candidates):
                    # Pick the location whose offset is closest to the
                    # matched theme's offset — locations with no offset
                    # available are treated as maximally far so they lose
                    # to any location that does have proximity data.
                    loc_name, lat, lon, _ = min(
                        loc_candidates,
                        key=lambda c: abs(c[3] - theme_offset) if c[3] is not None else float("inf"),
                    )
                else:
                    # No offset data available for correlation (older/
                    # non-Enhanced rows) — same behavior as before this fix.
                    loc_name, lat, lon, _ = loc_candidates[0]
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
                matched_theme = matched_theme or _gdelt_pick_theme(themes)
                clean_theme = matched_theme.replace("_", " ").replace("CRISISLEX", "").strip().title() or "News Event"

                severity = "critical" if tone < -5 else "warn" if tone < -2 else "info"
                events.append(_event(
                    source="GDELT",
                    tag=f"NEWS · {matched_theme.split('_')[0][:18].upper()}",
                    title=f"{clean_theme} — {loc_name}",
                    detail=(
                        f"Coverage from {domain}. Theme: {matched_theme}. Tone: {tone:.2f}. "
                        f"Theme and location are automatically extracted from the source article and "
                        f"matched by proximity within its text, not manually verified — for a long or "
                        f"multi-story article, the linked page may cover more than just this specific "
                        f"item."
                    ),
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


# ── OpenSky Network aircraft snapshot ────────────────────────────────────────
# NOTE: no longer called directly by the scheduler — confirmed (with valid
# OAuth2 credentials set) that OpenSky's /states/all ConnectTimeouts from
# Render's IP range regardless of auth, the same "this datacenter IP range
# specifically is blocked" pattern AIS hit first. The main backend now polls
# the ais-bridge service's /aircraft endpoint instead (see scheduler.py
# _poll_aircraft), the same fix already proven for AIS. This function is
# kept for local/non-Render deployments where direct OpenSky access isn't
# blocked, and because ais-bridge/app.py's own OpenSky fetch logic is
# intentionally a duplicate of this (that service has zero import
# dependency on the main backend, by design — see its own module docstring).
#
# Still entirely free: OpenSky requires the OAuth2 client-credentials flow
# (Log in at opensky-network.org -> Account -> API Clients -> create one, no
# card involved) instead of a plain API key, mirroring how ACLED's
# email+password OAuth flow already works in this file (see
# _get_acled_token above) -- same shape, different provider. Set
# OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET; if unset, falls back to the
# old anonymous request (works, just unreliable from a data-center IP).
OPENSKY_URL = "https://opensky-network.org/api/states/all"
OPENSKY_TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"

# Module-level token cache: {"access_token": str, "expires_at": datetime}
_opensky_token_cache: dict = {}


async def _get_opensky_token(client: httpx.AsyncClient, client_id: str, client_secret: str) -> str | None:
    global _opensky_token_cache
    now = datetime.utcnow()

    cached = _opensky_token_cache.get("access_token")
    expires_at = _opensky_token_cache.get("expires_at")
    if cached and expires_at and now < expires_at:
        return cached

    try:
        resp = await client.post(
            OPENSKY_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("access_token")
        expires_in = data.get("expires_in", 1800)   # OpenSky tokens are short-lived, ~30min
        if token:
            _opensky_token_cache = {
                "access_token": token,
                "expires_at": now + timedelta(seconds=expires_in - 60),  # refresh 1min early
            }
            logger.info("OpenSky: OAuth token acquired")
        return token
    except Exception as e:
        logger.error(f"OpenSky OAuth error: {type(e).__name__}: {e}")
        return None


# OpenSky state vector array indices (per their documented schema) —
# named here so the parsing below isn't a wall of magic numbers.
_OS_ICAO24, _OS_CALLSIGN, _OS_ORIGIN_COUNTRY = 0, 1, 2
_OS_LON, _OS_LAT, _OS_BARO_ALT = 5, 6, 7
_OS_ON_GROUND, _OS_VELOCITY, _OS_HEADING = 8, 9, 10
_OS_VERT_RATE = 11


async def fetch_opensky(
    client: httpx.AsyncClient,
    client_id: str = "",
    client_secret: str = "",
) -> list[dict]:
    """Fetch the current global OpenSky state-vector snapshot.

    Skips entries with no position fix (lat/lon null — aircraft OpenSky
    knows about but hasn't received a position report for recently) since
    those can't be plotted, same as vessel_store only keeping entries with
    lat/lon set.
    """
    headers = {}
    if client_id and client_secret:
        token = await _get_opensky_token(client, client_id, client_secret)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        # If the token fetch failed, fall through and try anonymously
        # rather than giving up the whole poll cycle — better a thin
        # anonymous result than nothing.

    try:
        resp = await client.get(OPENSKY_URL, headers=headers, timeout=httpx.Timeout(20, connect=8))
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        # type(e).__name__ matters here: a prior version of this log line
        # printed just "{e}", which came back completely blank for a
        # connection-level rejection on Render (see comment above) and
        # gave no signal to debug from. Always log the exception type too.
        logger.error(f"OpenSky fetch error: {type(e).__name__}: {e}")
        # Re-raise (rather than returning []) so the scheduler can tell an
        # actual failure apart from "network's fine, genuinely 0 aircraft
        # right now" and record it in aircraft_store for /sources to
        # surface — a silently-swallowed [] here would make persistent
        # connectivity failures invisible from the UI.
        raise

    states = data.get("states") or []
    aircraft = []
    for s in states:
        try:
            lat, lon = s[_OS_LAT], s[_OS_LON]
            if lat is None or lon is None:
                continue
            icao24 = s[_OS_ICAO24]
            if not icao24:
                continue
            callsign = (s[_OS_CALLSIGN] or "").strip()
            aircraft.append({
                "icao24": icao24,
                "callsign": callsign or icao24.upper(),
                "origin_country": s[_OS_ORIGIN_COUNTRY],
                "lat": lat,
                "lon": lon,
                "baro_altitude_m": s[_OS_BARO_ALT],
                "on_ground": bool(s[_OS_ON_GROUND]),
                "velocity_ms": s[_OS_VELOCITY],
                "heading": s[_OS_HEADING],
                "vertical_rate_ms": s[_OS_VERT_RATE],
                "last_update": datetime.utcnow().isoformat() + "Z",
            })
        except (IndexError, TypeError):
            continue

    logger.info(f"OpenSky: fetched {len(aircraft)} aircraft with position")
    return aircraft


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
