"""
remote_sensing_endpoints.py — direct-access endpoints for the Remote
Sensing tab (see services/gee_remote_sensing.py). Deliberately skips
the natural-language parsing / geocoding pipeline endpoints.py uses
for the Analyze tab — this is for someone who already knows which
tool and AOI they want, not someone typing a question in English.

Reuses the same BackgroundTasks + job_store polling pattern as
endpoints.py's /query, for the same reason: GEE's Python client calls
(.getInfo()) are blocking, so running them directly in a request
handler would tie up the event loop for however long the computation
takes. A separate job_store namespace (RS job IDs vs analysis query
job IDs) isn't needed since job_store keys everything by its own UUID
regardless of caller.
"""

import logging
import uuid
from typing import Dict, Any, Optional, List

from fastapi import APIRouter, HTTPException, BackgroundTasks, Request
from pydantic import BaseModel

from ..core.rate_limit import limiter
from ..core.job_store import job_store
from ..services import gee_remote_sensing as rs

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/remote-sensing", tags=["Remote Sensing"])

TOOLS = {
    "spectral_indices": {
        "label": "Spectral Indices", "needs_dates": True,
        "description": "NDVI, NDWI, MNDWI, NDBI, SAVI, EVI, NDSI — Sentinel-2, 10-20m.",
    },
    "terrain": {
        "label": "Terrain Analysis", "needs_dates": False,
        "description": "Elevation, slope, aspect — Copernicus DEM GLO-30, 30m.",
    },
    "lulc": {
        "label": "Land Cover Classification", "needs_dates": False,
        "description": "11-class land cover area breakdown — ESA WorldCover v200, 10m, 2021.",
    },
    "snow_cover": {
        "label": "Snow Cover (NDSI)", "needs_dates": True,
        "description": "Snow/ice-covered area — Sentinel-2 NDSI, 20m.",
    },
    "sar_backscatter": {
        "label": "SAR Backscatter", "needs_dates": True,
        "description": "VV/VH backscatter + Radar Vegetation Index — Sentinel-1 GRD, 20m, all-weather.",
    },
    "change_detection": {
        "label": "Change Detection", "needs_dates": False, "needs_two_periods": True,
        "description": "Diff any spectral index between two date ranges — Sentinel-2, 20m.",
    },
    "burn_severity": {
        "label": "Burn Severity (dNBR + RBR)", "needs_dates": False, "needs_two_periods": True,
        "description": "USGS FIREMON dNBR + RBR (Parks et al. 2014) burn-severity classification — Sentinel-2, 20m.",
    },
    "atmospheric_composition": {
        "label": "Atmospheric Composition", "needs_dates": True,
        "description": "NO2, SO2, CO, aerosol index — Sentinel-5P/TROPOMI, ~1.1km.",
    },
    "index_time_series": {
        "label": "Index Time Series", "needs_dates": True,
        "description": "Chart a spectral index across monthly/quarterly sub-periods — Sentinel-2, 20m.",
    },
    "land_surface_temperature": {
        "label": "Land Surface Temperature", "needs_dates": True,
        "description": "Surface (not air) temperature from thermal imagery — Landsat 8/9, 30m.",
    },
    "surface_water_dynamics": {
        "label": "Surface Water Dynamics", "needs_dates": False,
        "description": "Permanent vs seasonal water extent, 1984-2021 — JRC Global Surface Water, 30m.",
    },
    "supervised_classification": {
        "label": "Supervised Classification (ML)", "needs_dates": True, "needs_training_samples": True,
        "description": "Random Forest classification trained on your own training points, into your own classes — Sentinel-2, 10m.",
    },
    "dynamic_world": {
        "label": "Dynamic World (ML land cover)", "needs_dates": True,
        "description": "Google/WRI pretrained near-real-time land cover — 9 fixed classes, no training needed, shared model — 10m.",
    },
    "accuracy_assessment": {
        "label": "Accuracy Assessment", "needs_dates": False, "needs_reference_points": True,
        "description": "Confusion matrix / kappa (Congalton 1991) for LULC, Dynamic World, or Burn Severity, against your own reference points.",
    },
    "flood_mapping": {
        "label": "Flood Mapping (SAR)", "needs_dates": False, "needs_two_periods": True,
        "description": "Sentinel-1 SAR before/after flood extent — UN-SPIDER's Recommended Practice, all-weather/day-night, 10m.",
    },
    "soil_moisture": {
        "label": "Soil Moisture (SMAP)", "needs_dates": True,
        "description": "Surface soil moisture (sm_surface) — NASA SMAP L4, ~9km, single-period snapshot.",
    },
}


class RemoteSensingRequest(BaseModel):
    tool: str
    aoi_geojson: Dict[str, Any]
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    indices: Optional[List[str]] = None  # spectral_indices only
    index: Optional[str] = None  # change_detection only
    period1_start: Optional[str] = None  # change_detection
    period1_end: Optional[str] = None
    period2_start: Optional[str] = None
    period2_end: Optional[str] = None
    pre_start: Optional[str] = None  # burn_severity
    pre_end: Optional[str] = None
    post_start: Optional[str] = None
    post_end: Optional[str] = None
    interval: Optional[str] = "month"  # index_time_series
    training_samples: Optional[List[Dict[str, Any]]] = None  # supervised_classification: [{lat, lon, class_id, class_label}]
    num_trees: Optional[int] = None  # supervised_classification, defaults to rs.DEFAULT_NUM_TREES if omitted
    assess_tool: Optional[str] = None  # accuracy_assessment: which tool's classification to validate (lulc | dynamic_world | burn_severity)
    reference_points: Optional[List[Dict[str, Any]]] = None  # accuracy_assessment: [{lat, lon, true_class}]
    polarization: Optional[str] = None  # flood_mapping: 'VH' (default) or 'VV'


def _run_tool(request_id: uuid.UUID, req: RemoteSensingRequest):
    job_store.set(request_id, {"status": "processing", "stage": "computing", "progress_pct": 30})
    try:
        if req.tool == "spectral_indices":
            if not req.start_date or not req.end_date:
                raise ValueError("spectral_indices requires start_date and end_date.")
            result = rs.compute_spectral_indices(req.aoi_geojson, req.start_date, req.end_date, req.indices)
        elif req.tool == "terrain":
            result = rs.compute_terrain_analysis(req.aoi_geojson)
        elif req.tool == "lulc":
            result = rs.compute_lulc_classification(req.aoi_geojson)
        elif req.tool == "snow_cover":
            if not req.start_date or not req.end_date:
                raise ValueError("snow_cover requires start_date and end_date.")
            result = rs.compute_snow_cover(req.aoi_geojson, req.start_date, req.end_date)
        elif req.tool == "sar_backscatter":
            if not req.start_date or not req.end_date:
                raise ValueError("sar_backscatter requires start_date and end_date.")
            result = rs.compute_sar_backscatter(req.aoi_geojson, req.start_date, req.end_date)
        elif req.tool == "change_detection":
            if not all([req.index, req.period1_start, req.period1_end, req.period2_start, req.period2_end]):
                raise ValueError("change_detection requires index, period1_start, period1_end, period2_start, period2_end.")
            result = rs.compute_change_detection(req.aoi_geojson, req.index, req.period1_start, req.period1_end, req.period2_start, req.period2_end)
        elif req.tool == "burn_severity":
            if not all([req.pre_start, req.pre_end, req.post_start, req.post_end]):
                raise ValueError("burn_severity requires pre_start, pre_end, post_start, post_end.")
            result = rs.compute_burn_severity(req.aoi_geojson, req.pre_start, req.pre_end, req.post_start, req.post_end)
        elif req.tool == "atmospheric_composition":
            if not req.start_date or not req.end_date:
                raise ValueError("atmospheric_composition requires start_date and end_date.")
            result = rs.compute_atmospheric_composition(req.aoi_geojson, req.start_date, req.end_date)
        elif req.tool == "index_time_series":
            if not all([req.index, req.start_date, req.end_date]):
                raise ValueError("index_time_series requires index, start_date, end_date.")
            result = rs.compute_index_time_series(req.aoi_geojson, req.index, req.start_date, req.end_date, req.interval or "month")
        elif req.tool == "land_surface_temperature":
            if not req.start_date or not req.end_date:
                raise ValueError("land_surface_temperature requires start_date and end_date.")
            result = rs.compute_land_surface_temperature(req.aoi_geojson, req.start_date, req.end_date)
        elif req.tool == "surface_water_dynamics":
            result = rs.compute_surface_water_dynamics(req.aoi_geojson)
        elif req.tool == "supervised_classification":
            if not req.start_date or not req.end_date:
                raise ValueError("supervised_classification requires start_date and end_date.")
            if not req.training_samples:
                raise ValueError("supervised_classification requires training_samples: [{lat, lon, class_id, class_label}, ...].")
            result = rs.compute_supervised_classification(
                req.aoi_geojson, req.start_date, req.end_date, req.training_samples,
                req.num_trees or rs.DEFAULT_NUM_TREES,
            )
        elif req.tool == "dynamic_world":
            if not req.start_date or not req.end_date:
                raise ValueError("dynamic_world requires start_date and end_date.")
            result = rs.compute_dynamic_world_classification(req.aoi_geojson, req.start_date, req.end_date)
        elif req.tool == "accuracy_assessment":
            if not req.assess_tool:
                raise ValueError(f"accuracy_assessment requires assess_tool: one of {sorted(rs.ACCURACY_ASSESSABLE_TOOLS)}.")
            if not req.reference_points:
                raise ValueError("accuracy_assessment requires reference_points: [{lat, lon, true_class}, ...].")
            tool_params = {
                "start_date": req.start_date, "end_date": req.end_date,
                "pre_start": req.pre_start, "pre_end": req.pre_end,
                "post_start": req.post_start, "post_end": req.post_end,
            }
            result = rs.compute_accuracy_assessment(req.assess_tool, req.aoi_geojson, req.reference_points, tool_params)
        elif req.tool == "flood_mapping":
            if not all([req.pre_start, req.pre_end, req.post_start, req.post_end]):
                raise ValueError("flood_mapping requires pre_start, pre_end, post_start, post_end.")
            result = rs.compute_flood_mapping(req.aoi_geojson, req.pre_start, req.pre_end, req.post_start, req.post_end, req.polarization or "VH")
        elif req.tool == "soil_moisture":
            if not req.start_date or not req.end_date:
                raise ValueError("soil_moisture requires start_date and end_date.")
            result = rs.compute_soil_moisture(req.aoi_geojson, req.start_date, req.end_date)
        else:
            job_store.update(request_id, {"status": "failed", "error": f"Unknown tool: {req.tool}. Valid: {list(TOOLS)}"})
            return
    except ValueError as e:
        logger.warning(f"[{request_id}] Remote sensing validation error: {e}")
        job_store.update(request_id, {"status": "failed", "error": str(e)})
        return
    except Exception as e:
        logger.error(f"[{request_id}] Remote sensing computation error: {e}", exc_info=True)
        job_store.update(request_id, {"status": "failed", "error": f"Computation failed: {type(e).__name__}: {e}"})
        return

    job_store.update(request_id, {"status": "done", "stage": "done", "progress_pct": 100, "tool": req.tool, "result": result})
    logger.info(f"[{request_id}] Remote sensing tool '{req.tool}' complete.")


@router.get("/tools", summary="List available remote sensing tools")
async def list_tools():
    return {"tools": [{"id": k, **v} for k, v in TOOLS.items()]}


@router.post("/analyze", status_code=202, summary="Run a remote sensing tool on an AOI")
@limiter.limit("15/minute")
async def analyze(req: RemoteSensingRequest, background_tasks: BackgroundTasks, request: Request):
    if req.tool not in TOOLS:
        raise HTTPException(status_code=422, detail=f"Unknown tool: {req.tool}. Valid: {list(TOOLS)}")
    if not req.aoi_geojson:
        raise HTTPException(status_code=422, detail="aoi_geojson is required.")
    request_id = uuid.uuid4()
    job_store.set(request_id, {"status": "processing", "stage": "queued", "progress_pct": 0})
    background_tasks.add_task(_run_tool, request_id, req)
    return {"request_id": str(request_id)}


@router.get("/analyze/{request_id}", summary="Get remote sensing job status/result")
async def get_result(request_id: uuid.UUID):
    result = job_store.get(request_id)
    if not result:
        raise HTTPException(status_code=404, detail="Request ID not found.")
    status = result.get("status")
    if status == "failed":
        raise HTTPException(status_code=422, detail=result.get("error", "Processing failed."))
    if status != "done":
        return {"status": status, "progress_pct": result.get("progress_pct", 0)}
    return {"status": "done", "tool": result.get("tool"), "result": result.get("result")}
