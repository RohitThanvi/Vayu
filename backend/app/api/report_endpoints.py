import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from ..core.rate_limit import limiter
from fastapi.responses import Response
from pydantic import BaseModel

from ..services import gee_client
from ..services.report_generator import build_analysis_report, build_remote_sensing_report, ANALYSIS_SPECS
from ..services import gee_remote_sensing as rs
from ..services import timelapse_media
from ..services.satellite_imagery import (
    get_thumbnail_for_analysis, get_optical_thumbnail_with_coverage,
    get_change_map_thumbnail,
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
    before_image_meta, after_image_meta = None, None
    change_map_bytes = None
    wants_change_map = req.analysis_type in ("vegetation_change", "builtup_change", "water_change")
    if req.include_imagery:
        if req.analysis_type == "flood_detection":
            calls = [
                asyncio.to_thread(get_thumbnail_for_analysis, req.analysis_type, req.aoi_geojson, req.start_date),
                asyncio.to_thread(get_thumbnail_for_analysis, req.analysis_type, req.aoi_geojson, req.end_date),
            ]
            results = await asyncio.gather(*calls, return_exceptions=True)
            before_bytes = results[0] if not isinstance(results[0], Exception) else None
            after_bytes = results[1] if not isinstance(results[1], Exception) else None
        else:
            # Optical types go through the coverage-returning variant directly
            # (bypassing the byte-only dispatcher) so the report caption can
            # say what actually happened — whether the ±30-day window had to
            # widen, and how much of the AOI ended up with valid pixels —
            # instead of unconditionally claiming a clean ±30-day composite
            # regardless of what was actually achievable for that date (e.g.
            # a monsoon-season date can genuinely have far less cloud-free
            # coverage available than a dry-season one).
            #
            # The change-map fetch (when applicable) runs in this SAME
            # gather, not after it — it's an independent GEE computation
            # from the before/after imagery, so running it sequentially
            # afterward would add a full extra round-trip of latency to
            # every vegetation/builtup/water report for no reason.
            calls = [
                asyncio.to_thread(get_optical_thumbnail_with_coverage, req.aoi_geojson, req.start_date),
                asyncio.to_thread(get_optical_thumbnail_with_coverage, req.aoi_geojson, req.end_date),
            ]
            if wants_change_map:
                calls.append(asyncio.to_thread(
                    get_change_map_thumbnail, req.analysis_type, req.aoi_geojson, req.start_date, req.end_date))
            results = await asyncio.gather(*calls, return_exceptions=True)
            before_meta_raw = results[0] if not isinstance(results[0], Exception) else None
            after_meta_raw = results[1] if not isinstance(results[1], Exception) else None
            before_bytes = before_meta_raw["bytes"] if before_meta_raw else None
            after_bytes = after_meta_raw["bytes"] if after_meta_raw else None
            before_image_meta = (
                {"window_days": before_meta_raw["window_days"], "valid_pct": before_meta_raw["valid_pct"]}
                if before_meta_raw else None
            )
            after_image_meta = (
                {"window_days": after_meta_raw["window_days"], "valid_pct": after_meta_raw["valid_pct"]}
                if after_meta_raw else None
            )
            if wants_change_map:
                change_map_bytes = results[2] if not isinstance(results[2], Exception) else None
        labels = ("before", "after", "change_map") if wants_change_map else ("before", "after")
        for label, r in zip(labels, results):
            if isinstance(r, Exception):
                # Imagery is a nice-to-have on top of the metrics/findings, which
                # are already computed above — never fail the whole report over
                # a thumbnail fetch issue.
                logger.warning(f"report {label} imagery fetch failed, continuing without it: {r}")

    llm_synthesis = None
    try:
        # Pass the same hedged, deterministic findings text and stated
        # limitations the PDF itself shows — without this, the model had
        # nothing but bare numbers to work from and would invent its own
        # certainty language ("fresh construction", "confirms rapid
        # urbanization") that the deterministic text deliberately avoids
        # ("not direct confirmation of a physical construction... event").
        # The system prompt below tells it to stay within that framing;
        # this is what gives it something concrete to stay within.
        spec = ANALYSIS_SPECS.get(req.analysis_type, {})
        deterministic_findings = None
        try:
            if spec.get("findings_fn"):
                deterministic_findings = " ".join(spec["findings_fn"](metrics))
        except Exception as e:
            # Best-effort context enrichment for the LLM prompt, not required
            # for the report itself — but logged (not silently swallowed) so
            # a genuine findings_fn bug is still visible in production, even
            # though it won't fail the request.
            logger.debug(f"deterministic_findings context build failed for {req.analysis_type}, continuing without it: {e}")
        context = {
            "analysis_type": req.analysis_type, "metrics": metrics,
            "period": {"start_date": req.start_date, "end_date": req.end_date},
            "deterministic_findings": deterministic_findings,
            "stated_limitations": spec.get("limitations"),
        }
        llm_synthesis = await asyncio.to_thread(get_llm_synthesis, context)
    except Exception as e:
        logger.warning(f"report LLM synthesis failed, continuing without it: {e}")

    try:
        pdf_bytes = build_analysis_report(
            analysis_type=req.analysis_type, aoi_geojson=req.aoi_geojson,
            start_date=req.start_date, end_date=req.end_date, metrics=metrics,
            before_image_bytes=before_bytes, after_image_bytes=after_bytes,
            before_image_meta=before_image_meta, after_image_meta=after_image_meta,
            change_map_bytes=change_map_bytes,
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


class RemoteSensingReportRequest(BaseModel):
    tool: str
    aoi_geojson: Dict[str, Any]
    # The already-computed compute_*() result from a prior /remote-sensing/
    # analyze call — required, not optional (unlike /analysis's `metrics`
    # above). The frontend always has this in state by the time "Generate
    # Report" is clickable, and requiring it here avoids re-running the
    # full computation (including every index/period the user selected)
    # a second time just to build the PDF.
    result: Dict[str, Any]
    region_name: Optional[str] = None
    include_imagery: bool = True
    # Only the params needed to REBUILD the underlying image for a static
    # thumbnail (see gee_remote_sensing.get_report_thumbnail) — a subset
    # of RemoteSensingRequest's fields in remote_sensing_endpoints.py,
    # whichever the tool actually needs.
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    indices: Optional[List[str]] = None
    index: Optional[str] = None
    period1_start: Optional[str] = None
    period1_end: Optional[str] = None
    period2_start: Optional[str] = None
    period2_end: Optional[str] = None
    pre_start: Optional[str] = None
    pre_end: Optional[str] = None
    post_start: Optional[str] = None
    post_end: Optional[str] = None
    training_samples: Optional[List[Dict[str, Any]]] = None  # supervised_classification
    num_trees: Optional[int] = None  # supervised_classification
    polarization: Optional[str] = None  # flood_mapping
    composites: Optional[List[str]] = None  # spectral_composites
    frequency: Optional[str] = None  # timelapse
    layer: Optional[str] = None  # timelapse


@router.post("/remote-sensing", summary="Generate a scientific PDF report for a Spectra (remote sensing) result")
async def remote_sensing_report(req: RemoteSensingReportRequest):
    thumbnail_bytes = None
    frame_images = None
    if req.include_imagery and req.tool == "timelapse" and (req.result or {}).get("frames"):
        # A timelapse report embeds every frame, taken from the links the result already carries (no recomputation).
        try:
            frame_images = await asyncio.to_thread(timelapse_media.fetch_frame_images, req.result["frames"])
        except Exception as e:
            logger.warning(f"timelapse frame fetch failed, continuing without frame images: {e}")
            frame_images = [None] * len(req.result["frames"])
    elif req.include_imagery:
        try:
            thumbnail_bytes = await asyncio.to_thread(
                rs.get_report_thumbnail, req.tool, req.aoi_geojson,
                start_date=req.start_date, end_date=req.end_date,
                indices=req.indices, index=req.index,
                period1_start=req.period1_start, period1_end=req.period1_end,
                period2_start=req.period2_start, period2_end=req.period2_end,
                pre_start=req.pre_start, pre_end=req.pre_end,
                post_start=req.post_start, post_end=req.post_end,
                training_samples=req.training_samples, num_trees=req.num_trees,
                polarization=req.polarization, composites=req.composites,
            )
        except Exception as e:
            # Thumbnail is a nice-to-have for the report, not load-bearing —
            # the numeric results and methodology are already fully known
            # from req.result, so a failed image fetch shouldn't block the
            # PDF the way a failed core computation would.
            logger.warning(f"remote sensing report thumbnail fetch failed, continuing without it: {e}")

    try:
        pdf_bytes = build_remote_sensing_report(
            tool=req.tool, aoi_geojson=req.aoi_geojson, result=req.result,
            thumbnail_bytes=thumbnail_bytes, region_name=req.region_name, frame_images=frame_images,
        )
    except Exception as e:
        logger.error(f"remote sensing report generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Report generation failed: {e}")

    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="vayu_spectra_{req.tool}_report.pdf"'},
    )


@router.get("/business-brief", summary="One-document PDF brief of the Business tab (markets, commodities, risk, events, India, yield curve)")
@limiter.limit("6/minute")
async def business_brief(request: Request):
    """Built from the same cached data the screen shows; a section whose data is unavailable says so rather than failing the brief."""
    from datetime import datetime, timezone
    from ..services.intel import market_indices, commodity_prices, india_risk, yield_curve
    from ..services.intel.store import intel_store
    from ..services.business_brief import build_business_brief
    from . import intel_endpoints

    async def safe(coro, default):
        try:
            return await coro
        except Exception as e:  # a failing source degrades one section, not the whole brief
            logger.warning(f"business brief: section unavailable ({type(e).__name__})")
            return default

    risk = await safe(intel_endpoints.compute_risk_scores(), [])
    india = await safe(india_risk.get_india_risk(), None)
    snap = {
        "markets": market_indices.get_markets(), "commodities": commodity_prices.get_commodities(), "risk": risk,
        "events": intel_store.feed_response()["events"],
        "india": india_risk.attach_chokepoints(india, risk) if india else None,
        "yield_curve": await safe(yield_curve.get_yield_curve(), None),
    }
    pdf = await asyncio.to_thread(build_business_brief, snap)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="vayu-business-brief-{stamp}.pdf"'})
