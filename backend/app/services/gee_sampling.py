"""
gee_sampling.py - region reductions that stay honest for areas smaller than a coarse dataset's pixel.

The trap: Earth Engine's reduceRegion over a polygon that does not contain the CENTRE of any pixel can return null for every band (this is well known; the polygon is smaller than one SMAP pixel (~9 km) or one
MODIS LST pixel (1 km)). Callers that wrote `stats.get(key, 0) or 0` then reported a missing reading as a real zero: soil moisture 0.0, or a Vegetation Health Index of 0, which Kogan's classes call EXTREME DROUGHT.
Most user-drawn areas (a field, a village, a small district) are smaller than a SMAP pixel, so this hit most areas.

reduce_stats() tries the polygon first; if every value is null it samples the pixel under the AOI centroid and says so; if that is also null it reports no data (an empty dict, sampling None). Callers must treat
a missing value as None, never 0. It takes any object with reduceRegion()/getInfo() and any region with centroid(), so it is unit-tested offline with fakes (no Earth Engine in the dev sandbox).
Not yet run against live Earth Engine: whether Earth Engine includes a partly covered pixel for a given polygon is exactly what the centroid fallback is there to be independent of.
"""
from typing import Any, Dict, Optional, Tuple

SAMPLING_AOI = "aoi"
SAMPLING_CENTROID = "pixel_at_centroid"


def has_values(stats: Optional[Dict[str, Any]]) -> bool:
    """True if a reduceRegion result carries at least one non-null value."""
    return bool(stats) and any(v is not None for v in stats.values())


def reduce_stats(image: Any, region: Any, reducer: Any, scale: int, *, tile_scale: int = 4, max_pixels: float = 1e9) -> Tuple[Dict[str, Any], Optional[str]]:
    """(stats, sampling): the reducer over the AOI polygon, else over the pixel under the AOI centroid, else ({}, None)."""
    kw = dict(reducer=reducer, scale=scale, maxPixels=max_pixels, tileScale=tile_scale)
    stats = image.reduceRegion(geometry=region, bestEffort=True, **kw).getInfo() or {}
    if has_values(stats): return stats, SAMPLING_AOI
    stats = image.reduceRegion(geometry=region.centroid(maxError=100), **kw).getInfo() or {}
    if has_values(stats): return stats, SAMPLING_CENTROID
    return {}, None


def sampling_note(sampling: Optional[str], pixel_km: float, dataset: str) -> Optional[str]:
    """One honest sentence for the output when the value is for a single pixel rather than for the whole AOI."""
    if sampling != SAMPLING_CENTROID: return None
    return (f"The area of interest is smaller than one {dataset} pixel (about {pixel_km:g} km), so this value describes the pixel under the centre of the area, "
            f"not only the area you drew.")


def num(stats: Dict[str, Any], key: str, nd: int) -> Optional[float]:
    """A rounded statistic, or None when it is missing. Never 0 for missing."""
    v = stats.get(key)
    return None if v is None else round(v, nd)
