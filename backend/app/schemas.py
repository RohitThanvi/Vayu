from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any, List, Literal
import uuid

MetricType = Literal[
    "vegetation_change",
    "builtup_change",
    "water_change",
    "flood_detection",
    "fire_detection",
    "drought_index",
    "land_surface_temperature",
    "deforestation",
    "soil_moisture",
]


class QueryRequest(BaseModel):
    text: str = Field(..., min_length=5, max_length=500, example="How much green cover did Jaipur lose since 2020?")
    aoi_geojson: Optional[Dict[str, Any]] = Field(None, description="GeoJSON Polygon/MultiPolygon geometry.")
    options: Optional[Dict[str, Any]] = None

    @field_validator("text")
    @classmethod
    def sanitize_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Query text must not be empty.")
        return v

    @field_validator("aoi_geojson")
    @classmethod
    def validate_geojson(cls, v):
        if v is None:
            return v
        allowed = {"Polygon", "MultiPolygon", "Feature", "FeatureCollection"}
        if v.get("type") not in allowed:
            raise ValueError(f"aoi_geojson.type must be one of {allowed}")
        return v


class QueryInitiatedResponse(BaseModel):
    request_id: uuid.UUID
    status: str = "processing"
    message: str = "Analysis queued. Poll /api/v1/query/{request_id} for results."


class JobStatusResponse(BaseModel):
    request_id: uuid.UUID
    status: str
    stage: Optional[str] = None
    progress_pct: Optional[int] = None


class FinalQueryResponse(BaseModel):
    request_id: uuid.UUID
    status: str = "done"
    result_type: str = "analysis"   # "analysis" (the original GEE metrics pipeline) or "research" (web-search-grounded agent answer)
    metric: Optional[str] = None
    summary: Optional[str] = None
    insight: Optional[str] = None
    metrics: Optional[Dict[str, float]] = None
    geojson_url: Optional[str] = None
    tile_url: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    region: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None
    aoi_geojson: Optional[Dict[str, Any]] = None   # the AOI actually used — echoed back so the frontend can draw/reuse it even when it was auto-resolved server-side (geocoded from a place name), not drawn by hand
    # Research-agent-only fields — populated when result_type == "research"
    place_name: Optional[str] = None
    reasoning: Optional[str] = None
    radius_km: Optional[float] = None
    confidence: Optional[str] = None
    places: Optional[List[Dict[str, Any]]] = None   # 0+ candidate spots — see research_agent.py
    source_urls: Optional[List[str]] = None
    search_results_used: Optional[int] = None
    answer: Optional[str] = None                     # research agent: direct text answer (always present, even with no places)
    key_points: Optional[List[Dict[str, Any]]] = None   # [{point, urls}] — claims with their sources
    gaps: Optional[List[str]] = None                 # what the research could NOT establish
    diagnostics: Optional[Dict[str, Any]] = None     # rounds, queries, search provider, pages fetched/failed — for debugging "why did it fail"
    # Combined (web + satellite + OpenStreetMap) siting agent — see services/siting_agent.py
    ranking: Optional[List[Dict[str, Any]]] = None   # candidates ranked by a deterministic, auditable screening score
    use_case: Optional[str] = None
    web_summary: Optional[str] = None                # the web-research-only answer, kept alongside the data-backed recommendation
    tradeoffs: Optional[List[str]] = None
    conflicts: Optional[List[str]] = None            # web claims that disagree with measurements
    next_steps: Optional[List[str]] = None
    siting_caveats: Optional[List[str]] = None
    methodology: Optional[str] = None
    live_data_source: Optional[str] = None   # set when answered from Vayu's own live feeds (AIS/ADS-B/USGS/FIRMS), not web search


class StructuredQuery(BaseModel):
    metric: Optional[MetricType] = None
    in_scope: bool = True
    domain_relevant: bool = True   # false = not a geospatial question at all (chit-chat, unrelated request, or a prompt-injection attempt) — see llm_client._PARSE_SYSTEM
    region: Optional[str] = None
    aoi_geojson: Optional[Dict[str, Any]] = None
    start_date: str
    end_date: str
    resolution: int = Field(30, ge=10, le=500)
