"""
alert_engine.py — background loop that turns the risk-scoring engine from
query-only into push: periodically re-scores every watchlist region and
fires an alert (persisted + optionally WhatsApp-pushed) when a region
crosses its own configured risk_threshold.

Runs as a FastAPI lifespan background task, same pattern as
services/intel/scheduler.py.
"""

import asyncio
import logging
from typing import Optional

from . import db
from .risk_scoring import compute_risk_score

logger = logging.getLogger(__name__)

INTERVAL_AGRI_SCAN = 6 * 60 * 60  # 6 hours — satellite-derived indices don't move faster than this


class AgriAlertEngine:
    def __init__(self, whatsapp_notify=None):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._whatsapp_notify = whatsapp_notify  # optional async fn(phone, message)

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Agri alert engine started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("Agri alert engine stopped")

    async def _loop(self):
        while self._running:
            try:
                await self.scan_all_regions()
            except Exception as e:
                logger.error(f"Agri alert scan failed: {e}", exc_info=True)
            await asyncio.sleep(INTERVAL_AGRI_SCAN)

    async def scan_all_regions(self):
        regions = db.list_regions()
        logger.info(f"Agri alert engine: scanning {len(regions)} watchlist regions")
        for region in regions:
            await self._scan_region(region)

    async def _scan_region(self, region: dict):
        import json
        try:
            aoi = json.loads(region["aoi_geojson"])
            result = await asyncio.to_thread(compute_risk_score, aoi, None, region["id"])
        except Exception as e:
            logger.warning(f"Agri alert engine: scoring failed for region {region['id']}: {e}")
            return

        if result["risk_score"] >= region["risk_threshold"]:
            alert = db.create_alert(
                region_id=region["id"],
                risk_score=result["risk_score"],
                confidence=result["confidence"],
                reason=result["reason"],
                metrics=result["raw_metrics"],
            )
            logger.info(f"Agri ALERT fired: region={region['name']} score={result['risk_score']}")

            if region.get("phone") and self._whatsapp_notify:
                message = (
                    f"VAYU Agri Alert — {region['name']}\n"
                    f"Risk: {result['band'].upper()} ({result['risk_score']}/100, "
                    f"confidence {result['confidence']}%)\n{result['reason']}"
                )
                try:
                    await self._whatsapp_notify(region["phone"], message)
                except Exception as e:
                    logger.warning(f"WhatsApp notify failed for {region['id']}: {e}")


_engine: Optional[AgriAlertEngine] = None


def get_agri_engine(whatsapp_notify=None) -> AgriAlertEngine:
    global _engine
    if _engine is None:
        _engine = AgriAlertEngine(whatsapp_notify=whatsapp_notify)
    return _engine
