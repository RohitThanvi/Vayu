"""
global_layers.py — toggleable, whole-map satellite imagery layers (true
color, NDVI, SAR/microwave, thermal/IR), similar to the layer switcher in
ISRO's Bhuvan or Google Earth Engine's own Explorer.

Earth Engine's getMapId() tile URLs are inherently a {z}/{x}/{y} tile
template that Google computes lazily per-tile on request — a single call
against a date-filtered (not bounds-filtered) collection gives a URL usable
at any pan/zoom on the map, the same way the existing OpenWeatherMap tile
layers work. No per-viewport recomputation needed.

Each layer is built once and cached in memory with a TTL, since building
the composite + calling getMapId() is a real (if modest) GEE cost we
shouldn't repeat on every toggle click.
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import ee

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 12 * 60 * 60  # 12 hours — a background "current conditions" layer doesn't
                                    # need to be fresher than this; doubling from 6h halves how
                                    # often the expensive first-hit-after-expiry rebuild happens
_cache: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


def _cached(key: str, builder) -> Dict[str, Any]:
    with _lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry["cached_at"]) < CACHE_TTL_SECONDS:
            return entry["value"]

    value = builder()
    with _lock:
        _cache[key] = {"value": value, "cached_at": time.time()}
    return value


def _recent_s2_composite(days_back: int = 30):
    """Cloud-masked Sentinel-2 mosaic (most-recent-pixel-wins, not a
    median) over the last `days_back` days, global (not bounds-filtered) —
    GEE serves tiles lazily so this is fine for whole-map use, matching how
    the wind/temperature/pressure tile layers already work in this app.

    Deliberately mosaic(), not median(): for a 'what does this look like
    right now' display layer (not a change-detection or noise-robustness
    use case), a recency-sorted mosaic needs to find only the first
    non-masked pixel per tile location, while a median needs to reduce
    across every overlapping image at that location — real, measured
    difference in per-tile compute cost, which is what a user actually
    waits on when toggling this layer, not the (already cached) tile_url
    lookup itself. _mask_s2_clouds() is still applied first, so this is
    'the most recent cloud-free pixel', not literally whatever's newest
    regardless of quality."""
    from .gee_client import _mask_s2_clouds
    end = datetime.utcnow()
    start = end - timedelta(days=days_back)
    col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterDate(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        .map(_mask_s2_clouds)
        .sort("system:time_start", False)  # newest first, so mosaic() prefers recent pixels
    )
    return col.mosaic()


def _build_true_color() -> Dict[str, Any]:
    composite = _recent_s2_composite()
    # Same min:0,max:0.3 stretch fix as satellite_imagery.py — _mask_s2_clouds
    # rescales reflectance to 0-1, so the stretch must match that scale, not
    # the raw 0-10000 DN range.
    map_id = composite.getMapId({"bands": ["B4", "B3", "B2"], "min": 0, "max": 0.3, "gamma": 1.3})
    return {"tile_url": map_id["tile_fetcher"].url_format, "label": "True Color (Sentinel-2, ~30d)"}


def _build_ndvi() -> Dict[str, Any]:
    composite = _recent_s2_composite()
    ndvi = composite.normalizedDifference(["B8", "B4"])
    map_id = ndvi.getMapId({
        "min": -0.2, "max": 0.8,
        "palette": ["#a83232", "#d9a441", "#e8e88a", "#8fd453", "#1a7a1a"],
    })
    return {"tile_url": map_id["tile_fetcher"].url_format, "label": "NDVI Vegetation Index"}


def _build_sar() -> Dict[str, Any]:
    """Sentinel-1 SAR (VH, microwave) — Bhuvan-style 'microwave layer'.
    SAR sees through cloud cover, showing surface roughness/moisture rather
    than visible color.

    Kept as median(), unlike the other three layers here — radar
    backscatter has real per-pixel speckle noise, and reducing across
    multiple looks is a genuine, meaningful quality improvement for SAR
    specifically (the same reason flood_detection's own SAR pipeline
    applies a speckle filter), not just conservatism. That's a real
    speed-vs-quality tradeoff this layer keeps on the quality side, so
    it stays the slowest of the four to render. Window trimmed from 15
    to 12 days as a modest, non-quality-affecting speedup — Sentinel-1's
    revisit cycle is typically 6-12 days depending on location/orbit
    overlap, so 12 days still reliably captures multiple looks almost
    everywhere without carrying the same window 15 days did."""
    end = datetime.utcnow()
    start = end - timedelta(days=12)
    col = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterDate(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
        .select("VH")
    )
    composite = col.median()
    map_id = composite.getMapId({"min": -25, "max": 0})
    return {"tile_url": map_id["tile_fetcher"].url_format, "label": "SAR / Microwave (Sentinel-1)"}


def _build_thermal() -> Dict[str, Any]:
    """Landsat thermal band, converted to Celsius and colored — Bhuvan-style
    'IR layer'. Shows surface temperature, not a literal near-infrared band
    composite, since that's the thermal/IR product people actually expect
    visually (hot=red, cool=blue) rather than raw reflectance."""
    end = datetime.utcnow()
    start = end - timedelta(days=45)

    def to_celsius(img):
        lst = img.select("ST_B10").multiply(0.00341802).add(149.0).subtract(273.15)
        return lst.rename("LST_C").copyProperties(img, ["system:time_start"])

    col = (
        ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
        .filterDate(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        .filter(ee.Filter.lt("CLOUD_COVER", 30))
        .map(to_celsius)
        .sort("system:time_start", False)  # same mosaic-over-median speedup as _recent_s2_composite
    )
    composite = col.mosaic()
    map_id = composite.getMapId({
        "min": 0, "max": 45,
        "palette": ["#1a4d7a", "#4a9ec9", "#e8e88a", "#d97a41", "#8b2020"],
    })
    return {"tile_url": map_id["tile_fetcher"].url_format, "label": "Thermal / IR (Landsat LST)"}


_BUILDERS = {
    "true_color": _build_true_color,
    "ndvi": _build_ndvi,
    "sar": _build_sar,
    "thermal": _build_thermal,
}


def get_layer(layer_key: str) -> Optional[Dict[str, Any]]:
    builder = _BUILDERS.get(layer_key)
    if not builder:
        return None
    try:
        return _cached(layer_key, builder)
    except Exception as e:
        logger.error(f"global_layers: failed to build '{layer_key}': {e}", exc_info=True)
        raise
