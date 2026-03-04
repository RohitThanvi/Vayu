import logging
import uuid
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from ..schemas import (
    QueryRequest,
    QueryInitiatedResponse,
    JobStatusResponse,
    FinalQueryResponse,
)
from ..services import llm_client, gee_client, geoprocess
from ..core.job_store import job_store

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Geospatial Analysis"])

# ── Progress stages ─────────────────────────────────────────────────────────────
STAGES = {
    "queued":              (0,  "Request queued"),
    "llm_parsing":         (15, "Parsing natural language query"),
    "llm_parsed":          (25, "Query parsed successfully"),
    "gee_computing":       (35, "Running satellite computation"),
    "gee_computed":        (65, "Satellite analysis complete"),
    "geoprocessing":       (70, "Processing geospatial results"),
    "geoprocess_complete": (85, "Geospatial processing done"),
    "generating_summary":  (90, "Generating AI summary"),
    "done":                (100, "Analysis complete"),
}


def _update_stage(request_id: uuid.UUID, stage: str, extra: Dict[str, Any] = None):
    pct, label = STAGES.get(stage, (0, stage))
    data = {"status": "processing", "stage": stage, "progress_pct": pct, "stage_label": label}
    if extra:
        data.update(extra)
    job_store.update(request_id, data)
    logger.info(f"[{request_id}] Stage: {stage} ({pct}%)")


def process_geospatial_query(request_id: uuid.UUID, text: str, aoi_geojson: dict):
    logger.info(f"[{request_id}] Background processing started")
    job_store.set(request_id, {"status": "processing", "stage": "queued", "progress_pct": 0})

    # ── 1. Parse NL query ──────────────────────────────────────────────────────
    try:
        _update_stage(request_id, "llm_parsing")
        structured_query = llm_client.parse_natural_language_query(text)
        _update_stage(request_id, "llm_parsed")
        logger.info(f"[{request_id}] Metric={structured_query.metric}, Region={structured_query.region}")
    except Exception as e:
        logger.error(f"[{request_id}] LLM parse error: {e}", exc_info=True)
        job_store.update(request_id, {"status": "failed", "error": f"Query parsing failed: {str(e)}"})
        return

    # ── 2. Resolve AOI ────────────────────────────────────────────────────────
    effective_aoi = aoi_geojson or structured_query.aoi_geojson
    if not effective_aoi:
        job_store.update(request_id, {"status": "failed", "error": "No Area of Interest provided. Please draw one on the map."})
        return

    # ── 3. GEE Computation ────────────────────────────────────────────────────
    try:
        _update_stage(request_id, "gee_computing")

        metric_fn_map = {
            "vegetation_change": gee_client.compute_vegetation_change,
            "builtup_change": gee_client.compute_builtup_change,
            "water_change": gee_client.compute_water_change,
            "flood_detection": gee_client.compute_flood_detection,
            "fire_detection": gee_client.compute_fire_detection,
            "drought_index": gee_client.compute_drought_index,
            "land_surface_temperature": gee_client.compute_land_surface_temperature,
            "deforestation": gee_client.compute_deforestation,
            "soil_moisture": gee_client.compute_soil_moisture,
        }

        fn = metric_fn_map.get(structured_query.metric)
        if fn is None:
            job_store.update(request_id, {"status": "failed", "error": f"Unsupported metric: {structured_query.metric}"})
            return

        gee_results = fn(
            aoi=effective_aoi,
            start_date=structured_query.start_date,
            end_date=structured_query.end_date,
        )
        _update_stage(request_id, "gee_computed")
    except ValueError as e:
        logger.warning(f"[{request_id}] GEE validation error: {e}")
        job_store.update(request_id, {"status": "failed", "error": str(e)})
        return
    except Exception as e:
        logger.error(f"[{request_id}] GEE computation error: {e}", exc_info=True)
        job_store.update(request_id, {"status": "failed", "error": f"Satellite computation failed: {str(e)}"})
        return

    # ── 4. Geoprocessing ──────────────────────────────────────────────────────
    try:
        _update_stage(request_id, "geoprocessing")
        asset_urls = geoprocess.process_and_store_results(
            loss_mask_image=gee_results["ee_image"],
            aoi_geometry=gee_results["ee_geometry"],
            request_id=request_id,
        )
        _update_stage(request_id, "geoprocess_complete")
    except Exception as e:
        logger.error(f"[{request_id}] Geoprocess error: {e}", exc_info=True)
        job_store.update(request_id, {"status": "failed", "error": f"Geoprocessing failed: {str(e)}"})
        return

    # ── 5. AI Summary ─────────────────────────────────────────────────────────
    try:
        _update_stage(request_id, "generating_summary")
        final_metrics = gee_results["metrics"]
        summary = llm_client.generate_summary(structured_query, final_metrics)
        insight = llm_client.generate_insight(structured_query, final_metrics)
    except Exception as e:
        logger.warning(f"[{request_id}] Summary generation error: {e}")
        final_metrics = gee_results.get("metrics", {})
        summary = f"Analysis complete. Detected changes in {structured_query.region or 'selected area'}."
        insight = None

    # ── 6. Finalise ───────────────────────────────────────────────────────────
    job_store.update(request_id, {
        "status": "done",
        "stage": "done",
        "progress_pct": 100,
        "metric": structured_query.metric,
        "summary": summary,
        "insight": insight,
        "metrics": final_metrics,
        "geojson_url": asset_urls.get("geojson_url"),
        "tile_url": asset_urls.get("tile_url"),
        "start_date": structured_query.start_date,
        "end_date": structured_query.end_date,
        "region": structured_query.region,
    })
    logger.info(f"[{request_id}] Processing complete.")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/query",
    response_model=QueryInitiatedResponse,
    status_code=202,
    summary="Submit a geospatial analysis query",
)
async def create_query(query: QueryRequest, background_tasks: BackgroundTasks, request: Request):
    request_id = uuid.uuid4()
    job_store.set(request_id, {"status": "processing", "stage": "queued", "progress_pct": 0})
    background_tasks.add_task(
        process_geospatial_query, request_id, query.text, query.aoi_geojson
    )
    logger.info(f"[{request_id}] Query submitted: '{query.text[:80]}'")
    return QueryInitiatedResponse(request_id=request_id)


@router.get(
    "/query/{request_id}/status",
    response_model=JobStatusResponse,
    summary="Get lightweight job status (for polling)",
)
async def get_query_status(request_id: uuid.UUID):
    result = job_store.get(request_id)
    if not result:
        raise HTTPException(status_code=404, detail="Request ID not found.")
    return JobStatusResponse(
        request_id=request_id,
        status=result.get("status", "unknown"),
        stage=result.get("stage"),
        progress_pct=result.get("progress_pct"),
    )


@router.get(
    "/query/{request_id}",
    summary="Get full analysis result",
)
async def get_query_result(request_id: uuid.UUID):
    result = job_store.get(request_id)
    if not result:
        raise HTTPException(status_code=404, detail="Request ID not found.")

    status = result.get("status")

    if status == "failed":
        raise HTTPException(status_code=422, detail=result.get("error", "Processing failed."))

    if status != "done":
        return JSONResponse(
            status_code=202,
            content={
                "request_id": str(request_id),
                "status": status,
                "stage": result.get("stage"),
                "progress_pct": result.get("progress_pct", 0),
                "stage_label": result.get("stage_label", "Processing..."),
            },
        )

    return FinalQueryResponse(request_id=request_id, **{
        k: v for k, v in result.items()
        if k in FinalQueryResponse.model_fields
    })


@router.get("/metrics", summary="List all supported analysis metrics")
async def list_metrics():
    return {
        "metrics": [
            {"id": "vegetation_change",        "label": "Vegetation Change",          "description": "NDVI-based green cover loss/gain using Sentinel-2", "icon": "🌿"},
            {"id": "builtup_change",            "label": "Built-up Area Change",       "description": "Urban expansion using Dynamic World", "icon": "🏙️"},
            {"id": "water_change",              "label": "Water Body Change",          "description": "Surface water gain/loss using JRC dataset", "icon": "💧"},
            {"id": "flood_detection",           "label": "Flood Detection",            "description": "Flood mapping using SAR Sentinel-1", "icon": "🌊"},
            {"id": "fire_detection",            "label": "Fire & Burn Scar Detection", "description": "Active fire and burn scar mapping using MODIS/Sentinel-2", "icon": "🔥"},
            {"id": "drought_index",             "label": "Drought Index",              "description": "NDDI/VCI-based drought severity analysis", "icon": "🏜️"},
            {"id": "land_surface_temperature",  "label": "Land Surface Temperature",   "description": "LST analysis using Landsat 8/9 TIRS", "icon": "🌡️"},
            {"id": "deforestation",             "label": "Deforestation Analysis",     "description": "Tree cover loss using Hansen Global Forest Watch", "icon": "🌲"},
            {"id": "soil_moisture",             "label": "Soil Moisture",              "description": "Surface soil moisture using SMAP/Sentinel-1", "icon": "🌱"},
        ]
    }


@router.get("/admin/jobs/count", summary="Active job count (admin)", include_in_schema=False)
async def job_count():
    return {"active_jobs": job_store.size}


@router.get("/geojson/{request_id}", summary="Serve locally stored GeoJSON")
async def serve_geojson(request_id: uuid.UUID):
    from pathlib import Path
    from fastapi.responses import FileResponse
    local_path = Path(__file__).parent.parent.parent / "geojson_outputs" / f"{request_id}_change.geojson"
    if not local_path.exists():
        raise HTTPException(status_code=404, detail="GeoJSON file not found.")
    return FileResponse(local_path, media_type="application/geo+json")
