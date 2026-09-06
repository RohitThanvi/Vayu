"""
aggregator.py — pulls one consistent snapshot across every business/
intel source this project tracks, for the daily executive summary
email. Deliberately reads from the SAME live in-memory stores and
caches the rest of the app already uses (vessel_store, aircraft_store,
intel_store, commodity_prices, air_quality, supply_chain,
agri.db) rather than re-fetching anything from upstream APIs itself —
this is a read-only view over data that's already being kept warm by
the existing schedulers, so building the report costs nothing extra in
external API calls.

Returns a single plain dict, JSON-serializable, that both the LLM
summarizer and the email's chart-rendering code consume — one shared
snapshot, not fetched twice.
"""

import logging
from typing import Any, Dict

from ..intel.store import intel_store
from ..intel.vessel_store import vessel_store
from ..intel.aircraft_store import aircraft_store
from ..intel import commodity_prices, air_quality, supply_chain
from ..agri import db as agri_db

logger = logging.getLogger(__name__)


def gather_snapshot() -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {}

    # Commodities — full list, the email chart needs every mover, not just top N
    try:
        commodities = commodity_prices.get_commodities()
        snapshot["commodities"] = commodities.get("commodities", [])
        snapshot["commodities_error"] = commodities.get("last_error")
    except Exception as e:
        logger.warning(f"aggregator: commodities failed: {e}")
        snapshot["commodities"], snapshot["commodities_error"] = [], str(e)

    # Maritime + supply chain (already correlates vessel traffic with commodities)
    try:
        snapshot["vessel_stats"] = vessel_store.get_stats()
    except Exception as e:
        logger.warning(f"aggregator: vessel_stats failed: {e}")
        snapshot["vessel_stats"] = {}

    try:
        snapshot["supply_chain"] = supply_chain.get_supply_chain_correlation()
    except Exception as e:
        logger.warning(f"aggregator: supply_chain failed: {e}")
        snapshot["supply_chain"] = {"chokepoints": []}

    # Aviation
    try:
        snapshot["aircraft_stats"] = aircraft_store.get_stats()
    except Exception as e:
        logger.warning(f"aggregator: aircraft_stats failed: {e}")
        snapshot["aircraft_stats"] = {}

    # Air quality — aggregate rather than all ~800 stations (worst 5 + national avg)
    try:
        aqi = air_quality.get_stations()
        stations = aqi.get("stations", [])
        worst = sorted([s for s in stations if s.get("aqi") is not None], key=lambda s: -s["aqi"])[:5]
        avg_aqi = round(sum(s["aqi"] for s in stations) / len(stations), 1) if stations else None
        snapshot["air_quality"] = {"station_count": len(stations), "national_avg_aqi": avg_aqi, "worst_stations": worst, "last_error": aqi.get("last_error")}
    except Exception as e:
        logger.warning(f"aggregator: air_quality failed: {e}")
        snapshot["air_quality"] = {"station_count": 0, "worst_stations": []}

    # Global intel events (USGS/FIRMS/GDELT/ACLED) — stats + a few notable recent ones
    try:
        snapshot["intel_stats"] = intel_store.get_stats()
        snapshot["intel_notable"] = intel_store.query(severities=["critical", "warn"], limit=8)
    except Exception as e:
        logger.warning(f"aggregator: intel_store failed: {e}")
        snapshot["intel_stats"], snapshot["intel_notable"] = {}, []

    # Agri watchlist — regions registered + their most recent stored alert
    # (NOT a fresh GEE recompute per region here — that's real per-region
    # compute cost multiplied by however many regions are watched, which
    # doesn't belong on every daily email send; this reports the latest
    # already-computed reading, same as what the in-app watchlist shows)
    try:
        regions = agri_db.list_regions()
        agri_summary = []
        for r in regions:
            alerts = agri_db.list_alerts(region_id=r["id"], limit=1)
            agri_summary.append({
                "name": r["name"], "crop": r.get("crop"),
                "latest_score": alerts[0]["risk_score"] if alerts else None,
                "latest_reason": alerts[0]["reason"] if alerts else None,
            })
        snapshot["agri_watchlist"] = agri_summary
    except Exception as e:
        logger.warning(f"aggregator: agri_watchlist failed: {e}")
        snapshot["agri_watchlist"] = []

    return snapshot
