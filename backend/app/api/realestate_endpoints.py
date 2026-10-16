"""
realestate_endpoints.py — the Business tab's "Real Estate" (Location Intelligence) view.

Same BackgroundTasks + job_store polling pattern as the other long-running tools: measuring a location
needs several Earth Engine computations, so the HTTP request returns a job id immediately and the
browser polls for the result.
"""

import asyncio
import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field

from ..core.job_store import job_store
from ..core.rate_limit import limiter
from ..services import location_intel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/realestate", tags=["Real Estate"])


class LocationIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    query: Optional[str] = Field(default=None, max_length=160, description="Place name, or 'lat, lon'")
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)
    # Optional, user-typed prices in INR per sq ft. Vayu never fetches or estimates prices; these are shown back as
    # "you entered". The ceiling only catches typos (e.g. an extra zero or a total price typed into a per-sq-ft box).
    price_per_sqft: Optional[float] = Field(default=None, gt=0, le=1_000_000)
    circle_rate_per_sqft: Optional[float] = Field(default=None, gt=0, le=1_000_000)


class AssessRequest(BaseModel):
    locations: List[LocationIn] = Field(min_length=1, max_length=location_intel.MAX_LOCATIONS)
    radius_km: float = Field(default=location_intel.DEFAULT_RADIUS_KM, ge=location_intel.MIN_RADIUS_KM, le=location_intel.MAX_RADIUS_KM)
    profile: str = "balanced"


def _run(request_id: uuid.UUID, req: AssessRequest) -> None:
    job_store.set(request_id, {"status": "processing", "stage": "starting", "progress_pct": 2, "stage_label": "Starting"})

    def progress(label: str, pct: int) -> None:
        job_store.update(request_id, {"stage_label": label, "progress_pct": pct})

    try:
        result = asyncio.run(location_intel.assess_locations(
            [l.model_dump() for l in req.locations], req.radius_km, req.profile, progress))
    except ValueError as e:
        job_store.update(request_id, {"status": "failed", "error": str(e)})
        return
    except Exception as e:
        logger.error(f"[{request_id}] location intelligence failed: {e}", exc_info=True)
        job_store.update(request_id, {"status": "failed", "error": f"Analysis failed: {type(e).__name__}"})
        return
    job_store.update(request_id, {"status": "done", "stage_label": "Done", "progress_pct": 100, "result": result})


@router.get("/profiles", summary="Scoring profiles and their group weights")
async def profiles():
    return {"profiles": [{"id": k, "label": v["label"], "weights": v["weights"]} for k, v in location_intel.PROFILES.items()],
            "groups": {k: g["label"] for k, g in location_intel.GROUPS.items()},
            "max_locations": location_intel.MAX_LOCATIONS}


@router.post("/assess", status_code=202, summary="Score one or more locations on liveability, growth and hazard resilience")
@limiter.limit("6/minute")
async def assess(req: AssessRequest, background_tasks: BackgroundTasks, request: Request):
    if req.profile not in location_intel.PROFILES:
        raise HTTPException(status_code=422, detail=f"Unknown profile '{req.profile}'. Valid: {list(location_intel.PROFILES)}")
    if not any((l.query or l.name or (l.lat is not None and l.lon is not None)) for l in req.locations):
        raise HTTPException(status_code=422, detail="Give each location a name or coordinates.")
    request_id = uuid.uuid4()
    job_store.set(request_id, {"status": "processing", "stage": "queued", "progress_pct": 0})
    background_tasks.add_task(_run, request_id, req)
    return {"request_id": str(request_id)}


@router.get("/assess/{request_id}", summary="Job status / result")
async def result(request_id: uuid.UUID):
    job = job_store.get(request_id)
    if not job:
        raise HTTPException(status_code=404, detail="Request ID not found (results are kept only for a limited time).")
    if job.get("status") == "failed":
        raise HTTPException(status_code=422, detail=job.get("error", "Processing failed."))
    if job.get("status") != "done":
        return {"status": job.get("status"), "progress_pct": job.get("progress_pct", 0), "stage_label": job.get("stage_label")}
    return {"status": "done", "result": job.get("result")}
