import asyncio
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ..services import gee_client
from ..services.report_generator import build_analysis_report, build_agri_risk_report
from ..services.agri.risk_scoring import compute_risk_score
from ..services.agri.baseline import compute_seasonal_baseline
from ..services.satellite_imagery import (
    get_thumbnail_for_analysis, get_optical_thumbnail,
    get_ndvi_thumbnail, get_nddi_thumbnail, get_soil_moisture_thumbnail,
)
from ..services.llm_client import get_llm_synthesis
from .. schemas import MetricType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/report", tags=["Reports"])

_COMPUTE_FN = {
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


class AnalysisReportRequest(BaseModel):
    analysis_type: MetricType
    aoi_geojson: Dict[str, Any]
    start_date: str
    end_date: str
    # Optional: metrics already computed by a prior /query call, to avoid
    # recomputing against GEE a second time just to build the PDF. If
    # omitted, the analysis is recomputed fresh from the AOI/dates.
    metrics: Optional[Dict[str, Any]] = None
    include_imagery: bool = True


class AgriRiskReportRequest(BaseModel):
    aoi_geojson: Dict[str, Any]
    as_of: Optional[str] = None
    region_name: Optional[str] = None
    region_id: Optional[str] = None
    include_imagery: bool = True


@router.post("/analysis", summary="Generate a scientific PDF report for one of the 9 satellite analyses")
async def analysis_report(req: AnalysisReportRequest):
    fn = _COMPUTE_FN.get(req.analysis_type)
    if not fn:
        raise HTTPException(status_code=400, detail=f"Unknown analysis_type: {req.analysis_type}")

    metrics = req.metrics
    if metrics is None:
        try:
            result = fn(aoi=req.aoi_geojson, start_date=req.start_date, end_date=req.end_date)
            metrics = result["metrics"]
        except Exception as e:
            logger.error(f"report analysis recompute failed: {e}", exc_info=True)
            raise HTTPException(status_code=422, detail=f"Could not compute analysis for report: {e}")

    before_bytes, after_bytes = None, None
    if req.include_imagery:
        results = await asyncio.gather(
            asyncio.to_thread(get_thumbnail_for_analysis, req.analysis_type, req.aoi_geojson, req.start_date),
            asyncio.to_thread(get_thumbnail_for_analysis, req.analysis_type, req.aoi_geojson, req.end_date),
            return_exceptions=True,
        )
        before_bytes = results[0] if not isinstance(results[0], Exception) else None
        after_bytes = results[1] if not isinstance(results[1], Exception) else None
        for label, r in zip(("before", "after"), results):
            if isinstance(r, Exception):
                # Imagery is a nice-to-have on top of the metrics/findings, which
                # are already computed above — never fail the whole report over
                # a thumbnail fetch issue.
                logger.warning(f"report {label} imagery fetch failed, continuing without it: {r}")

    llm_synthesis = None
    try:
        context = {
            "analysis_type": req.analysis_type, "metrics": metrics,
            "period": {"start_date": req.start_date, "end_date": req.end_date},
        }
        llm_synthesis = await asyncio.to_thread(get_llm_synthesis, context)
    except Exception as e:
        logger.warning(f"report LLM synthesis failed, continuing without it: {e}")

    try:
        pdf_bytes = build_analysis_report(
            analysis_type=req.analysis_type, aoi_geojson=req.aoi_geojson,
            start_date=req.start_date, end_date=req.end_date, metrics=metrics,
            before_image_bytes=before_bytes, after_image_bytes=after_bytes,
            llm_synthesis=llm_synthesis,
        )
    except Exception as e:
        logger.error(f"report generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Report generation failed: {e}")

    filename = f"vayu_{req.analysis_type}_report.pdf"
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/agri-risk", summary="Generate a scientific PDF report for an agri risk score")
async def agri_risk_report(req: AgriRiskReportRequest):
    try:
        risk_result = compute_risk_score(aoi=req.aoi_geojson, as_of=req.as_of, region_id=req.region_id)
    except Exception as e:
        logger.error(f"agri risk report recompute failed: {e}", exc_info=True)
        raise HTTPException(status_code=422, detail=f"Could not compute risk score for report: {e}")

    image_bytes = None
    ndvi_bytes = None
    nddi_bytes = None
    moisture_bytes = None
    moisture_legend_range = None
    if req.include_imagery:
        as_of = risk_result.get("period", {}).get("end_date")
        results = await asyncio.gather(
            asyncio.to_thread(get_optical_thumbnail, req.aoi_geojson, as_of),
            asyncio.to_thread(get_ndvi_thumbnail, req.aoi_geojson, as_of),
            asyncio.to_thread(get_nddi_thumbnail, req.aoi_geojson, as_of),
            asyncio.to_thread(get_soil_moisture_thumbnail, req.aoi_geojson, as_of),
            return_exceptions=True,
        )
        labels = ("true-color", "NDVI", "NDDI", "soil-moisture")
        image_bytes, ndvi_bytes, nddi_bytes, moisture_result = (
            r if not isinstance(r, Exception) else None for r in results
        )
        if moisture_result:
            moisture_bytes = moisture_result["bytes"]
            moisture_legend_range = (moisture_result["min"], moisture_result["max"])
        for label, r in zip(labels, results):
            if isinstance(r, Exception):
                logger.warning(f"agri risk report {label} thumbnail failed, continuing without it: {r}")

    baseline_result = None
    try:
        as_of = risk_result.get("period", {}).get("end_date")
        baseline_result = await asyncio.to_thread(compute_seasonal_baseline, req.aoi_geojson, as_of)
    except Exception as e:
        logger.warning(f"agri risk report baseline fetch failed, continuing without it: {e}")

    llm_synthesis = None
    try:
        context = {
            "risk_score": risk_result.get("risk_score"), "band": risk_result.get("band"),
            "confidence": risk_result.get("confidence"), "sub_scores": risk_result.get("sub_scores"),
            "reason": risk_result.get("reason"), "region_name": req.region_name or "the AOI",
            "period": risk_result.get("period"),
            "seasonal_context": (
                {"status": baseline_result.get("status"), "z_score": baseline_result.get("z_score")}
                if baseline_result and baseline_result.get("seasonal_normal_ndvi") is not None else None
            ),
        }
        llm_synthesis = await asyncio.to_thread(get_llm_synthesis, context)
    except Exception as e:
        logger.warning(f"agri risk report LLM synthesis failed, continuing without it: {e}")

    try:
        pdf_bytes = build_agri_risk_report(
            aoi_geojson=req.aoi_geojson, risk_result=risk_result, region_name=req.region_name,
            image_bytes=image_bytes, ndvi_image_bytes=ndvi_bytes, nddi_image_bytes=nddi_bytes,
            moisture_image_bytes=moisture_bytes, moisture_legend_range=moisture_legend_range,
            baseline_result=baseline_result, llm_synthesis=llm_synthesis,
        )
    except Exception as e:
        logger.error(f"agri risk report generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Report generation failed: {e}")

    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="vayu_agri_risk_report.pdf"'},
    )
