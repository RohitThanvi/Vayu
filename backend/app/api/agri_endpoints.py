import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..services.agri import db, mandi, groundwater
from ..services.agri.risk_scoring import compute_risk_score
from ..services.agri.baseline import compute_seasonal_baseline
from ..services.agri.rollup import get_rollup
from ..services.agri.whatsapp import build_twiml_reply, handle_inbound_message

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agri", tags=["Agriculture"])


# ── Schemas ──────────────────────────────────────────────────────────────────

class RiskScoreRequest(BaseModel):
    aoi_geojson: Dict[str, Any]
    as_of: Optional[str] = None
    region_id: Optional[str] = None


class BaselineRequest(BaseModel):
    aoi_geojson: Dict[str, Any]
    as_of: Optional[str] = None
    years_back: int = 5


class CreateRegionRequest(BaseModel):
    name: str
    aoi_geojson: Dict[str, Any]
    crop: Optional[str] = None
    risk_threshold: float = Field(default=60.0, ge=0, le=100)
    owner_role: str = "farmer"
    phone: Optional[str] = None


class FeedbackRequest(BaseModel):
    alert_id: str
    accurate: bool
    comment: Optional[str] = None


# ── Core intelligence ────────────────────────────────────────────────────────

@router.post("/risk-score", summary="Composite 0-100 agricultural risk score for an AOI")
async def risk_score(req: RiskScoreRequest):
    try:
        return compute_risk_score(aoi=req.aoi_geojson, as_of=req.as_of, region_id=req.region_id)
    except Exception as e:
        logger.error(f"risk_score endpoint failed: {e}", exc_info=True)
        raise HTTPException(status_code=422, detail=f"Risk scoring failed: {e}")


@router.post("/baseline", summary="Multi-year seasonal-normal NDVI comparison for an AOI")
async def baseline(req: BaselineRequest):
    try:
        return compute_seasonal_baseline(aoi=req.aoi_geojson, as_of=req.as_of, years_back=req.years_back)
    except Exception as e:
        logger.error(f"baseline endpoint failed: {e}", exc_info=True)
        raise HTTPException(status_code=422, detail=f"Baseline computation failed: {e}")


# ── Watchlist / alerting ─────────────────────────────────────────────────────

@router.post("/regions", summary="Register a region to watch for alerts (any AOI, generalized)")
async def create_region(req: CreateRegionRequest):
    return db.create_region(
        name=req.name, aoi_geojson=req.aoi_geojson, crop=req.crop,
        risk_threshold=req.risk_threshold, owner_role=req.owner_role, phone=req.phone,
    )


@router.get("/regions", summary="List watched regions")
async def list_regions(owner_role: Optional[str] = None):
    return {"regions": db.list_regions(owner_role=owner_role)}


@router.delete("/regions/{region_id}", summary="Remove a watched region")
async def delete_region(region_id: str):
    ok = db.delete_region(region_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Region not found")
    return {"deleted": True}


@router.get("/regions/{region_id}/alerts", summary="Alert history for a region")
async def region_alerts(region_id: str, limit: int = 50):
    return {"alerts": db.list_alerts(region_id=region_id, limit=limit)}


@router.get("/alerts", summary="All recent alerts across watched regions")
async def all_alerts(limit: int = 100):
    return {"alerts": db.list_alerts(limit=limit)}


# ── Ground truth / trust ─────────────────────────────────────────────────────

@router.post("/feedback", summary="Report whether an alert was accurate (feeds future confidence scores)")
async def submit_feedback(req: FeedbackRequest):
    alert = db.get_alert(req.alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return db.create_feedback(alert_id=req.alert_id, accurate=req.accurate, comment=req.comment)


@router.get("/feedback/accuracy", summary="Rolling alert accuracy rate from farmer/officer feedback")
async def feedback_accuracy(region_id: Optional[str] = None):
    return db.feedback_accuracy_rate(region_id=region_id)


# ── Role-based views ─────────────────────────────────────────────────────────

@router.get("/rollup", summary="Role-based view: 'officer' (per-region detail) or 'district' (aggregate)")
async def rollup(role: str = "officer"):
    if role not in ("officer", "district"):
        raise HTTPException(status_code=400, detail="role must be 'officer' or 'district'")
    return get_rollup(role=role)


# ── Coverage breadth ─────────────────────────────────────────────────────────

@router.get("/mandi-price", summary="Mandi (market) price overlay for a commodity")
async def mandi_price(commodity: Optional[str] = None, state: Optional[str] = None,
                       district: Optional[str] = None, limit: int = 20):
    return await mandi.get_mandi_prices(commodity=commodity, state=state, district=district, limit=limit)


@router.post("/groundwater-trend", summary="Groundwater depletion/rising trend for an AOI (GRACE, regional-scale)")
async def groundwater_trend(req: BaselineRequest):
    try:
        return groundwater.compute_groundwater_trend(aoi=req.aoi_geojson, years_back=req.years_back)
    except Exception as e:
        logger.error(f"groundwater_trend endpoint failed: {e}", exc_info=True)
        raise HTTPException(status_code=422, detail=f"Groundwater trend failed: {e}")


# ── WhatsApp bot (last-mile delivery) ────────────────────────────────────────

@router.post("/whatsapp/webhook", summary="Twilio WhatsApp inbound webhook", include_in_schema=False)
async def whatsapp_webhook(request: Request):
    form = await request.form()
    body_text = form.get("Body", "")
    reply_text = await handle_inbound_message(body_text)
    return Response(content=build_twiml_reply(reply_text), media_type="application/xml")
