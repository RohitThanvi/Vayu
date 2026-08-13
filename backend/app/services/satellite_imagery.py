"""
satellite_imagery.py — fetches actual satellite imagery thumbnails (not
just derived metrics/masks) for embedding in PDF reports, so a report can
show the real before/after scene a finding is based on, not just numbers.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import ee
import httpx

from .gee_client import _polygon_geometry, _mask_s2_clouds, _cap_end_date

logger = logging.getLogger(__name__)

THUMB_DIMENSIONS = 640


def _fetch_thumb_bytes(image: ee.Image, region: ee.Geometry, vis_params: Dict[str, Any]) -> Optional[bytes]:
    try:
        url = image.getThumbURL({
            "region": region, "dimensions": THUMB_DIMENSIONS, "format": "png", **vis_params,
        })
        resp = httpx.get(url, timeout=30)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        logger.warning(f"satellite thumbnail fetch failed: {e}")
        return None


def get_optical_thumbnail(aoi: Dict[str, Any], center_date: str, days_window: int = 30) -> Optional[bytes]:
    """Sentinel-2 true-color (B4/B3/B2) cloud-masked median composite,
    centered on center_date. Used for most analysis types — the imagery a
    person would recognize as "what the satellite actually saw"."""
    region = _polygon_geometry(aoi)
    center_dt = datetime.strptime(center_date, "%Y-%m-%d")
    start = ee.Date((center_dt - timedelta(days=days_window)).strftime("%Y-%m-%d"))
    end = _cap_end_date((center_dt + timedelta(days=days_window)).strftime("%Y-%m-%d"))

    col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(region)
        .filterDate(start, end)
        .map(_mask_s2_clouds)
    )
    if col.size().getInfo() == 0:
        return None
    composite = col.median().clip(region)
    return _fetch_thumb_bytes(
        composite, region,
        {"bands": ["B4", "B3", "B2"], "min": 0, "max": 3000, "gamma": 1.3},
    )


def get_sar_thumbnail(aoi: Dict[str, Any], center_date: str, days_window: int = 15) -> Optional[bytes]:
    """Sentinel-1 SAR (VH polarization) grayscale composite — used for flood
    detection, where the report's own methodology is SAR-based, so the
    embedded imagery should be the same data type the finding is drawn from."""
    region = _polygon_geometry(aoi)
    center_dt = datetime.strptime(center_date, "%Y-%m-%d")
    start = ee.Date((center_dt - timedelta(days=days_window)).strftime("%Y-%m-%d"))
    end = _cap_end_date((center_dt + timedelta(days=days_window)).strftime("%Y-%m-%d"))

    col = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(region)
        .filterDate(start, end)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
        .select("VH")
    )
    if col.size().getInfo() == 0:
        return None
    composite = col.median().clip(region)
    return _fetch_thumb_bytes(composite, region, {"min": -25, "max": 0})


def get_thumbnail_for_analysis(analysis_type: str, aoi: Dict[str, Any], date: str) -> Optional[bytes]:
    if analysis_type == "flood_detection":
        return get_sar_thumbnail(aoi, date)
    return get_optical_thumbnail(aoi, date)
