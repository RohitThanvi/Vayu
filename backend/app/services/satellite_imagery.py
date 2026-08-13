"""
satellite_imagery.py — fetches actual satellite imagery thumbnails (not
just derived metrics/masks) for embedding in PDF reports, so a report can
show the real before/after scene a finding is based on, not just numbers.
"""

import io
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import ee
import httpx
from PIL import Image as PILImage

from .gee_client import _polygon_geometry, _mask_s2_clouds, _cap_end_date

logger = logging.getLogger(__name__)

THUMB_DIMENSIONS = 640
MIN_VALID_PIXEL_FRACTION = 0.35  # below this, widen the window and try again


def _valid_pixel_fraction(image: ee.Image, region: ee.Geometry, band: str, scale: int) -> float:
    """Fraction of pixels within the AOI that have real (non-masked) data
    for the given band — used to detect a composite that's mostly cloud-
    masked/no-data before it gets embedded as a near-blank thumbnail."""
    try:
        stats = (
            image.select(band).mask()
            .reduceRegion(reducer=ee.Reducer.mean(), geometry=region, scale=scale,
                          maxPixels=1e9, bestEffort=True, tileScale=4)
            .getInfo()
        )
        return stats.get(band) or 0.0
    except Exception as e:
        logger.warning(f"valid-pixel-fraction check failed, proceeding anyway: {e}")
        return 1.0  # don't block the thumbnail over a diagnostic-check failure


def _flatten_to_opaque_png(png_bytes: bytes) -> bytes:
    """Composites any transparent/masked pixels onto a white background
    before handing the image to reportlab. GEE returns masked pixels (both
    outside the AOI polygon and cloud-masked areas within it) as a
    transparent PNG alpha channel — reportlab's image embedding does not
    reliably composite that transparency against the page background, and
    can render it as solid black instead. Flattening here guarantees a
    clean white background regardless of how the PDF library handles
    alpha, independent of whatever caused the transparency in the first
    place."""
    try:
        img = PILImage.open(io.BytesIO(png_bytes)).convert("RGBA")
        background = PILImage.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3])  # use alpha channel as mask
        out = io.BytesIO()
        background.save(out, format="PNG")
        return out.getvalue()
    except Exception as e:
        logger.warning(f"thumbnail flatten-to-opaque failed, using raw bytes: {e}")
        return png_bytes


def _fetch_thumb_bytes(image: ee.Image, region: ee.Geometry, vis_params: Dict[str, Any]) -> Optional[bytes]:
    try:
        url = image.getThumbURL({
            "region": region, "dimensions": THUMB_DIMENSIONS, "format": "png", **vis_params,
        })
        resp = httpx.get(url, timeout=30)
        resp.raise_for_status()
        return _flatten_to_opaque_png(resp.content)
    except Exception as e:
        logger.warning(f"satellite thumbnail fetch failed: {e}")
        return None


def get_optical_thumbnail(aoi: Dict[str, Any], center_date: str, days_window: int = 30) -> Optional[bytes]:
    """Sentinel-2 true-color (B4/B3/B2) cloud-masked median composite,
    centered on center_date. Used for most analysis types — the imagery a
    person would recognize as "what the satellite actually saw".

    Progressively widens the date window if cloud cover leaves too little
    valid data across the AOI — a large, irregular multi-vertex AOI can
    genuinely fail to get adequate cloud-free coverage in a narrow window,
    which would otherwise silently produce a mostly-blank thumbnail."""
    region = _polygon_geometry(aoi)
    center_dt = datetime.strptime(center_date, "%Y-%m-%d")

    for window in (days_window, days_window * 2, days_window * 3):
        start = ee.Date((center_dt - timedelta(days=window)).strftime("%Y-%m-%d"))
        end = _cap_end_date((center_dt + timedelta(days=window)).strftime("%Y-%m-%d"))

        col = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(region)
            .filterDate(start, end)
            .map(_mask_s2_clouds)
        )
        if col.size().getInfo() == 0:
            continue
        composite = col.median().clip(region)

        valid_frac = _valid_pixel_fraction(composite, region, "B4", scale=100)
        if valid_frac < MIN_VALID_PIXEL_FRACTION and window < days_window * 3:
            logger.info(f"optical thumbnail: only {valid_frac:.0%} valid coverage at \u00b1{window}d, widening window")
            continue

        return _fetch_thumb_bytes(
            composite, region,
            # _mask_s2_clouds() (applied above via the collection .map()) already
            # divides surface reflectance by 10000, rescaling it from the raw
            # ~0-10000 DN range down to ~0.0-1.0. Stretching against a 0-3000 max
            # (the RAW scale) here was the actual bug: every real pixel value
            # (~0.0-0.4) was negligible against 3000 and rendered as pure black,
            # with only rare saturated/anomalous pixels bright enough to show as
            # white flecks — exactly the black-with-white-speckle pattern seen in
            # the report. 0.3 is the standard stretch max for the already-rescaled
            # 0-1 reflectance range.
            {"bands": ["B4", "B3", "B2"], "min": 0, "max": 0.3, "gamma": 1.3},
        )
    return None


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
