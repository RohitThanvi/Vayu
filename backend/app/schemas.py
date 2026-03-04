from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any, Literal
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
    metric: str
    summary: str
    insight: Optional[str] = None
    metrics: Dict[str, float]
    geojson_url: Optional[str] = None
    tile_url: Optional[str] = None
    start_date: str
    end_date: str
    region: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


class StructuredQuery(BaseModel):
    metric: MetricType
    region: Optional[str] = None
    aoi_geojson: Optional[Dict[str, Any]] = None
    start_date: str
    end_date: str
    resolution: int = Field(30, ge=10, le=500)
