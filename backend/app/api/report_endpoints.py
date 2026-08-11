import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ..services import gee_client
from ..services.report_generator import build_analysis_report, build_agri_risk_report
from ..services.agri.risk_scoring import compute_risk_score
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


class AgriRiskReportRequest(BaseModel):
    aoi_geojson: Dict[str, Any]
    as_of: Optional[str] = None
    region_name: Optional[str] = None
    region_id: Optional[str] = None


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

    try:
        pdf_bytes = build_analysis_report(
            analysis_type=req.analysis_type, aoi_geojson=req.aoi_geojson,
            start_date=req.start_date, end_date=req.end_date, metrics=metrics,
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

    try:
        pdf_bytes = build_agri_risk_report(
            aoi_geojson=req.aoi_geojson, risk_result=risk_result, region_name=req.region_name,
        )
    except Exception as e:
        logger.error(f"agri risk report generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Report generation failed: {e}")

    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="vayu_agri_risk_report.pdf"'},
    )
