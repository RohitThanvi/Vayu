"""
Intelligence feed API endpoints.

REST:
  GET  /api/v1/intel/events          — paginated event feed with filters
  GET  /api/v1/intel/events/aoi      — events within a bounding box
  GET  /api/v1/intel/stats           — store statistics
  GET  /api/v1/intel/sources         — available sources and status
  GET  /api/v1/intel/aircraft        — currently tracked aircraft (adsb.lol, via ais-bridge)
  GET  /api/v1/intel/aircraft/stats  — aviation tracking statistics
  GET  /api/v1/intel/satellites/tle  — cached satellite orbital elements (CelesTrak)
  GET  /api/v1/intel/commodities     — cached global commodity prices (Yahoo Finance)
  GET  /api/v1/intel/air-quality/cpcb-stations — cached real-time CPCB Air Quality Index (India)
  GET  /api/v1/intel/supply-chain-correlation — chokepoint traffic status vs. related commodity moves
  GET  /api/v1/intel/wind-field      — animated wind vector grid (U/V components)

WebSocket:
  WS   /api/v1/intel/ws              — real-time event stream

WebSocket message format (server → client):
  {
    "type": "event",
    "data": { ...IntelEvent }
  }

  {
    "type": "ping",
    "ts": "2026-01-01T00:00:00Z"
  }

Client can send:
  { "type": "filter", "sources": ["USGS","NASA FIRMS"], "severities": ["critical","warn"] }
  { "type": "ping" }
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, HTTPException

from ..services.intel.store import intel_store
from ..services.intel.scheduler import get_scheduler
from ..services.intel.vessel_store import vessel_store, CATEGORY_LABELS, CHOKEPOINTS
from ..services.intel.aircraft_store import aircraft_store
from ..services.intel import satellite_tle
from ..services.intel import commodity_prices
from ..services.intel import air_quality
from ..services.intel import supply_chain
from ..services.intel import dark_vessels
from ..services.intel import edgar_exposure
from ..services.intel import sanctions
from ..services.intel import geo_tone as geo_tone_mod
from ..services.intel import seismic_disruption
from ..services.intel import macro as macro_mod
from ..services.intel import economic_blocs
from ..services.intel import timeseries_store
from ..services.intel import business_risk
from ..services.intel import strategic_sites
from ..services.weather.wind_field import wind_field_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/intel", tags=["intelligence"])


# ── REST endpoints ─────────────────────────────────────────────────────────────

@router.get("/events", summary="Get paginated intelligence events")
async def get_events(
    sources: Optional[str] = Query(None, description="Comma-separated: USGS,NASA FIRMS,ACLED,GDELT"),
    severities: Optional[str] = Query(None, description="Comma-separated: info,warn,critical"),
    tags: Optional[str] = Query(None, description="Comma-separated tag keywords"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    source_list   = [s.strip() for s in sources.split(",")] if sources else None
    severity_list = [s.strip() for s in severities.split(",")] if severities else None
    tag_list      = [t.strip() for t in tags.split(",")] if tags else None

    events = intel_store.query(
        sources=source_list,
        severities=severity_list,
        tags=tag_list,
        limit=limit,
        offset=offset,
    )
    return {
        "count": len(events),
        "offset": offset,
        "events": events,
    }


@router.get("/events/aoi", summary="Get events within a bounding box")
async def get_events_aoi(
    min_lat: float = Query(..., description="Minimum latitude"),
    min_lon: float = Query(..., description="Minimum longitude"),
    max_lat: float = Query(..., description="Maximum latitude"),
    max_lon: float = Query(..., description="Maximum longitude"),
    sources: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    if min_lat >= max_lat or min_lon >= max_lon:
        raise HTTPException(status_code=422, detail="Invalid bounding box.")

    source_list = [s.strip() for s in sources.split(",")] if sources else None
    events = intel_store.query(
        sources=source_list,
        bbox=(min_lat, min_lon, max_lat, max_lon),
        limit=limit,
    )

    # Summary counts per source
    summary: dict = {}
    for e in events:
        summary[e["source"]] = summary.get(e["source"], 0) + 1

    return {
        "bbox": {"min_lat": min_lat, "min_lon": min_lon,
                 "max_lat": max_lat, "max_lon": max_lon},
        "count": len(events),
        "by_source": summary,
        "events": events,
    }


@router.get("/stats", summary="Intelligence store statistics")
async def get_stats():
    return intel_store.get_stats()


@router.get("/sources", summary="Available intelligence sources and status")
async def get_sources():
    stats = intel_store.get_stats()
    by_source = stats.get("by_source", {})
    sources = [
        {"id": "USGS",       "name": "USGS Earthquake",   "status": "live",    "auth": False, "count": by_source.get("USGS", 0),       "interval_min": 5},
        {"id": "NASA FIRMS", "name": "NASA FIRMS Fire",   "status": "live",    "auth": False, "count": by_source.get("NASA FIRMS", 0), "interval_min": 15},
        {"id": "GDELT",      "name": "GDELT News Events", "status": "live",    "auth": False, "count": by_source.get("GDELT", 0),      "interval_min": 10},
        {"id": "ACLED",      "name": "ACLED Conflict",    "status": "standby", "auth": True,  "count": by_source.get("ACLED", 0),      "interval_min": 60},
        {"id": "adsb.lol",   "name": "adsb.lol Aviation",  "status": "error" if aircraft_store.get_stats().get("last_error") else "live", "auth": False, "count": aircraft_store.get_stats().get("active_aircraft", 0), "interval_min": 1.5, "last_error": aircraft_store.get_stats().get("last_error"), "last_success_at": aircraft_store.get_stats().get("last_success_at")},
        {"id": "CelesTrak",  "name": "CelesTrak Satellites", "status": "live", "auth": False, "count": satellite_tle.get_satellites().get("count", 0), "interval_min": 360},
        {"id": "CPCB AQI",   "name": "CPCB Air Quality (India)", "status": "error" if air_quality.get_stations().get("last_error") else "live", "auth": True, "count": len(air_quality.get_stations().get("stations", [])), "interval_min": 60, "last_error": air_quality.get_stations().get("last_error")},
        {"id": "AISHub",     "name": "AISHub Maritime",   "status": "planned", "auth": True,  "count": 0, "interval_min": 5},
        {"id": "GDACS",      "name": "GDACS Disasters",   "status": "planned", "auth": False, "count": 0, "interval_min": 30},
        {"id": "ISRO",       "name": "ISRO Bhuvan",       "status": "planned", "auth": True,  "count": 0, "interval_min": 60},
    ]
    return {"sources": sources, "total_events": stats.get("current_events", 0)}


# ── WebSocket endpoint ─────────────────────────────────────────────────────────

@router.websocket("/ws")
async def intel_websocket(websocket: WebSocket):
    """
    Real-time intelligence event stream.

    On connect: sends last 50 events immediately as a snapshot.
    Then streams new events as they arrive from any source.
    Client can send filter messages to narrow the stream.
    """
    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    intel_store.subscribe(queue)

    # Active filter state for this connection
    active_filters: dict = {
        "sources": None,
        "severities": None,
    }

    logger.info(f"WebSocket connected: {websocket.client}")

    try:
        # Send snapshot of recent events immediately — per-source fair
        # share, not a plain "last 50 overall" that a noisy source can
        # dominate (see get_snapshot()'s docstring for the confirmed
        # real-world case this fixes: GDELT events being crowded out).
        snapshot = intel_store.get_snapshot()
        await websocket.send_json({
            "type": "snapshot",
            "count": len(snapshot),
            "data": snapshot,
        })

        # Ping task — keep connection alive
        async def send_pings():
            while True:
                await asyncio.sleep(30)
                try:
                    await websocket.send_json({
                        "type": "ping",
                        "ts": datetime.utcnow().isoformat() + "Z",
                        "store_size": intel_store.get_stats()["current_events"],
                    })
                except Exception:
                    break

        # Event broadcast task — push new events from queue
        async def send_events():
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    # Apply client-side filter
                    src_ok = (active_filters["sources"] is None or
                              event["source"] in active_filters["sources"])
                    sev_ok = (active_filters["severities"] is None or
                              event["severity"] in active_filters["severities"])
                    if src_ok and sev_ok:
                        await websocket.send_json({"type": "event", "data": event})
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    break

        # Receive task — handle client messages (filters, pings)
        async def receive_messages():
            while True:
                try:
                    raw = await websocket.receive_text()
                    msg = json.loads(raw)
                    if msg.get("type") == "filter":
                        active_filters["sources"] = msg.get("sources")
                        active_filters["severities"] = msg.get("severities")
                        await websocket.send_json({
                            "type": "filter_ack",
                            "active_filters": active_filters,
                        })
                    elif msg.get("type") == "ping":
                        await websocket.send_json({"type": "pong"})
                except (json.JSONDecodeError, KeyError):
                    continue
                except Exception:
                    break

        await asyncio.gather(
            send_pings(),
            send_events(),
            receive_messages(),
            return_exceptions=True,
        )

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {websocket.client}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        intel_store.unsubscribe(queue)


# ── Maritime / Logistics Vessel Tracking ──────────────────────────────────────

@router.get("/vessels", summary="Get currently tracked vessels")
async def get_vessels(
    category: Optional[str] = Query(
        None, description="Filter by: TANKER, CARGO, PASSENGER, FISHING, OTHER"
    ),
    min_lat: Optional[float] = Query(None),
    min_lon: Optional[float] = Query(None),
    max_lat: Optional[float] = Query(None),
    max_lon: Optional[float] = Query(None),
    limit: int = Query(1000, ge=1, le=5000),
):
    bbox = None
    if all(v is not None for v in [min_lat, min_lon, max_lat, max_lon]):
        bbox = (min_lat, min_lon, max_lat, max_lon)

    vessels = vessel_store.query(category=category, bbox=bbox, limit=limit)
    return {
        "count": len(vessels),
        "vessels": vessels,
        "category_labels": CATEGORY_LABELS,
    }


@router.get("/vessels/stats", summary="Maritime tracking statistics")
async def get_vessel_stats():
    return vessel_store.get_stats()


@router.get("/vessels/chokepoints", summary="Monitored maritime chokepoint regions")
async def get_chokepoints():
    from ..services.intel.vessel_store import CHOKEPOINTS
    return {
        "chokepoints": [
            {"id": name, "bbox": bbox}
            for name, bbox in CHOKEPOINTS.items()
        ]
    }


# ── Aviation Tracking (adsb.lol, via ais-bridge) ────────────────────────────────

@router.get("/aircraft", summary="Get currently tracked aircraft")
async def get_aircraft(
    min_lat: Optional[float] = Query(None),
    min_lon: Optional[float] = Query(None),
    max_lat: Optional[float] = Query(None),
    max_lon: Optional[float] = Query(None),
    on_ground: Optional[bool] = Query(None, description="Filter to airborne (false) or on-ground (true) only"),
    limit: int = Query(2000, ge=1, le=8000),
):
    bbox = None
    if all(v is not None for v in [min_lat, min_lon, max_lat, max_lon]):
        bbox = (min_lat, min_lon, max_lat, max_lon)

    aircraft = aircraft_store.query(bbox=bbox, on_ground=on_ground, limit=limit)
    return {
        "count": len(aircraft),
        "aircraft": aircraft,
    }


@router.get("/aircraft/stats", summary="Aviation tracking statistics")
async def get_aircraft_stats():
    return aircraft_store.get_stats()


# ── Satellite Tracking (CelesTrak TLEs, propagated client-side) ───────────────

@router.get("/satellites/tle", summary="Cached satellite orbital elements (TLEs)")
async def get_satellite_tles():
    """Returns raw TLE data for a curated set of satellites (space stations
    + CelesTrak's 'visual' brightest-objects group). Position is NOT
    computed here — the frontend propagates each satellite's live position
    from these elements via SGP4 (satellite.js), refreshed independently of
    this endpoint. Cached server-side; refreshes every ~6h since TLEs don't
    meaningfully change faster than that for display purposes."""
    return satellite_tle.get_satellites()


# ── Commodity price ticker (Yahoo Finance, keyless) ─────────────────────────

@router.get("/commodities", summary="Cached global commodity prices")
async def get_commodities():
    """Global commodity futures prices (crude oil, natural gas, metals,
    agri commodities) for the marquee ticker. NOT MCX real-time data —
    MCX's live feed is a paid exchange subscription with no free/legal
    alternative; this uses Yahoo Finance's unofficial keyless chart API
    instead (real futures prices, ~15-20min delayed). Refreshed every
    few hours server-side and cached — see services/intel/commodity_prices.py."""
    return commodity_prices.get_commodities()


# ── Air quality (CPCB, India-only, free data.gov.in key) ───────────────────

@router.get("/air-quality/cpcb-stations", summary="Cached real-time CPCB Air Quality Index stations")
async def get_air_quality():
    """Real-time AQI from CPCB's monitoring network (~800+ India stations,
    hourly). India-only, matching CPCB's actual coverage — not a global
    layer like the other intel sources. Each station's overall AQI is the
    max of its reported pollutant sub-indices (CPCB's own National AQI
    convention). Refreshed hourly server-side and cached — see
    services/intel/air_quality.py. Empty stations list + a populated
    last_error means AQI_API_KEY isn't configured or CPCB's API is
    unreachable this cycle, not that there's genuinely no data.

    Deliberately NOT at /air-quality — that path is already the existing
    point-lookup endpoint (?lat=&lon=, OpenWeatherMap-backed) a few lines
    below. Registering both under the same path would have made this one
    silently intercept every call to the older endpoint, since FastAPI
    matches routes in declaration order."""
    return air_quality.get_stations()


# ── Supply-chain correlation (chokepoint disruption + related commodities) ──

@router.get("/supply-chain-correlation", summary="Chokepoint traffic status vs. related commodity price moves")
async def get_supply_chain_correlation():
    """For each of the 7 monitored maritime chokepoints: current vessel
    count vs. its own 14-day baseline (status: normal/elevated/disrupted/
    insufficient_history — see services/intel/timeseries_store.py), plus
    the day-over-day price move of commodities that route heavily
    through it (see services/intel/supply_chain.py for the mapping and
    why it's real-world approximation, not a measured trade-flow stat).
    A plain-language narrative sentence is included per chokepoint.
    'insufficient_history' is expected for the first ~2 days after this
    feature ships — baselines need real history to accumulate."""
    return supply_chain.get_supply_chain_correlation()


# ── New business-intelligence features (all free/open data sources) ────────

@router.get("/edgar-exposure/{chokepoint}", summary="Recent SEC filings mentioning a chokepoint (SEC EDGAR full-text search)")
async def get_edgar_exposure(chokepoint: str, days_back: int = 14):
    """Which public companies have recently disclosed exposure (in an
    8-K/10-K/10-Q) to a given monitored chokepoint — e.g. 'strait_of_hormuz'.
    Free, no API key; see services/intel/edgar_exposure.py."""
    if chokepoint not in CHOKEPOINTS:
        raise HTTPException(status_code=404, detail=f"Unknown chokepoint. Valid: {list(CHOKEPOINTS)}")
    return await edgar_exposure.exposure_for_chokepoint(chokepoint, days_back=days_back)


@router.get("/dark-vessels", summary="Vessels that went dark (AIS gap) near a chokepoint and reappeared elsewhere")
async def get_dark_vessels():
    """Pure logic on the AIS stream already tracked — no new data
    source. See services/intel/dark_vessels.py for the detection
    method and its false-positive guard."""
    return {"flags": dark_vessels.list_flags(), "stats": dark_vessels.get_stats()}


@router.get("/sanctions-screen", summary="Currently tracked vessels matched against the OFAC SDN list")
async def get_sanctions_screen():
    """Name-match only (see services/intel/sanctions.py for why, and
    why that's a real limitation to read match_confidence against)."""
    all_vessels = vessel_store.query(limit=5000)
    hits = await sanctions.screen_vessels(all_vessels)
    return {"hits": hits, "list_status": sanctions.get_list_status()}


@router.get("/geo-tone", summary="GDELT regional sentiment, aggregated from already-tracked events into grid cells")
async def get_geo_tone():
    gdelt_events = intel_store.query(sources=["GDELT"], limit=1000)
    return {"regions": geo_tone_mod.compute_tone_regions(gdelt_events)}


@router.get("/seismic-disruption", summary="Recent earthquakes near a monitored chokepoint")
async def get_seismic_disruption(min_magnitude: float = 5.0):
    usgs_events = intel_store.query(sources=["USGS"], limit=500)
    return {"disruptions": seismic_disruption.find_disruptions(usgs_events, min_magnitude=min_magnitude)}


@router.get("/macro", summary="Free macro-economic context (FRED + World Bank)")
async def get_macro():
    return await macro_mod.get_macro_snapshot()


@router.get("/business-risk", summary="Unified 0-100 risk score per chokepoint, blending traffic/tone/seismic/sanctions")
async def get_business_risk():
    """See services/intel/business_risk.py for the exact weights —
    deliberately simple/auditable rather than an opaque model."""
    gdelt_events = intel_store.query(sources=["GDELT"], limit=1000)
    usgs_events = intel_store.query(sources=["USGS"], limit=500)
    scores = []
    for cp_id, ((min_lat, min_lon), (max_lat, max_lon)) in CHOKEPOINTS.items():
        vessels_in_area = vessel_store.query(bbox=(min_lat, min_lon, max_lat, max_lon))
        scores.append(await business_risk.score_chokepoint(
            cp_id, len(vessels_in_area), gdelt_events, usgs_events, vessels_in_area,
        ))
    scores.sort(key=lambda s: s["score"], reverse=True)
    return {"chokepoints": scores}


@router.get("/strategic-sites", summary="Major ports/refineries/mines with live nearby-signal updates (earthquakes, dark vessels, news tone, recent headlines)")
async def get_strategic_sites(site_type: Optional[str] = None):
    """site_type: 'port' | 'refinery' | 'mine' | omit for all. Curated
    list — see services/intel/strategic_sites.py. Each site's 'signals'
    array only includes things actually happening near it right now,
    not a global dump."""
    if site_type and site_type not in ("port", "refinery", "mine"):
        raise HTTPException(status_code=422, detail="site_type must be one of: port, refinery, mine")
    usgs_events = intel_store.query(sources=["USGS"], limit=500)
    gdelt_events = intel_store.query(sources=["GDELT"], limit=1000)
    dark_flags = dark_vessels.list_flags()
    return {"sites": strategic_sites.list_sites_with_signals(usgs_events, gdelt_events, dark_flags, site_type)}


# ── Historical data, for the Analysis tab (charts, not just current values) ──

@router.get("/commodities/history", summary="Historical daily closes for a commodity, for charting")
async def get_commodity_history_endpoint(symbol: str, range: str = "3mo"):  # noqa: A002 (matches query param name)
    return await commodity_prices.get_history(symbol, range)


@router.get("/macro/history", summary="Historical FRED series (US) for charting")
async def get_fred_history_endpoint(series: str, months: int = 24):
    return await macro_mod.get_fred_history(series, months)


@router.get("/macro/history/worldbank", summary="Historical World Bank indicator (global or a specific country) for charting")
async def get_world_bank_history_endpoint(indicator: str, region: str = "global", years: int = 15):
    return await macro_mod.get_world_bank_history(indicator, region, years)


@router.get("/chokepoint-traffic-history", summary="Historical vessel-count series for a chokepoint, for charting")
async def get_chokepoint_traffic_history_endpoint(chokepoint: str, days: int = 14):
    if chokepoint not in CHOKEPOINTS:
        raise HTTPException(status_code=404, detail=f"Unknown chokepoint. Valid: {list(CHOKEPOINTS)}")
    return {"chokepoint": chokepoint, "points": timeseries_store.get_chokepoint_history(chokepoint, days)}


# ── Economic blocs (G7/G20/BRICS/ASEAN) — built on data already integrated above ──

@router.get("/economic-blocs", summary="List available economic blocs and their members")
async def list_economic_blocs():
    return {"blocs": [economic_blocs.get_bloc_info(b) for b in economic_blocs.get_bloc_ids()]}


@router.get("/economic-blocs/{bloc_id}/macro", summary="GDP growth + inflation per member country (World Bank)")
async def get_economic_bloc_macro(bloc_id: str):
    if bloc_id not in economic_blocs.get_bloc_ids():
        raise HTTPException(status_code=404, detail=f"Unknown bloc. Valid: {economic_blocs.get_bloc_ids()}")
    return await economic_blocs.get_bloc_macro(bloc_id)


@router.get("/economic-blocs/{bloc_id}/tone", summary="GDELT news tone scoped to this bloc's name/members")
async def get_economic_bloc_tone(bloc_id: str):
    if bloc_id not in economic_blocs.get_bloc_ids():
        raise HTTPException(status_code=404, detail=f"Unknown bloc. Valid: {economic_blocs.get_bloc_ids()}")
    return economic_blocs.get_bloc_tone(bloc_id)


@router.get("/economic-blocs/{bloc_id}/commodities", summary="Tracked commodities this bloc's members are major producers/exporters of (editorial)")
async def get_economic_bloc_commodities(bloc_id: str):
    if bloc_id not in economic_blocs.get_bloc_ids():
        raise HTTPException(status_code=404, detail=f"Unknown bloc. Valid: {economic_blocs.get_bloc_ids()}")
    return economic_blocs.get_bloc_commodities(bloc_id)


@router.get("/economic-blocs/{bloc_id}/exposure", summary="Recent SEC filings mentioning this bloc (SEC EDGAR full-text search)")
async def get_economic_bloc_exposure(bloc_id: str, days_back: int = 14):
    if bloc_id not in economic_blocs.get_bloc_ids():
        raise HTTPException(status_code=404, detail=f"Unknown bloc. Valid: {economic_blocs.get_bloc_ids()}")
    return await economic_blocs.get_bloc_exposure(bloc_id, days_back)


@router.get("/wind-field", summary="Animated wind vector grid (U/V components)")
async def get_wind_field():
    data = wind_field_store.get()
    if data is None:
        raise HTTPException(status_code=503, detail="wind field not yet available — refreshes shortly after startup")
    return data


@router.get("/air-quality", summary="Air quality (PM2.5, PM10, US AQI) at a point")
async def get_air_quality_endpoint(lat: float, lon: float):
    from ..services.weather.air_quality import get_air_quality
    result = await get_air_quality(lat, lon)
    if "error" in result:
        raise HTTPException(status_code=502, detail=f"Air quality lookup failed: {result['error']}")
    return result
