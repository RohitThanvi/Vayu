"""
Intelligence feed API endpoints.

REST:
  GET  /api/v1/intel/events          — paginated event feed with filters
  GET  /api/v1/intel/events/aoi      — events within a bounding box
  GET  /api/v1/intel/stats           — store statistics
  GET  /api/v1/intel/sources         — available sources and status

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
from ..services.intel.vessel_store import vessel_store, CATEGORY_LABELS

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
        {"id": "OpenSky",    "name": "OpenSky Aviation",  "status": "planned", "auth": False, "count": 0, "interval_min": 5},
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
        # Send snapshot of recent events immediately
        snapshot = intel_store.get_all(limit=50)
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
    from ..services.intel.ais_stream import CHOKEPOINTS
    return {
        "chokepoints": [
            {"id": name, "bbox": bbox}
            for name, bbox in CHOKEPOINTS.items()
        ]
    }
