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


def get_optical_thumbnail_with_coverage(aoi: Dict[str, Any], center_date: str, days_window: int = 30) -> Optional[Dict[str, Any]]:
    """Same composite as get_optical_thumbnail(), but also returns how it
    was actually built: the window the code ended up using (may be wider
    than requested — see the widening loop below) and the fraction of the
    AOI that ended up with valid (non-masked) pixels. A report caption that
    unconditionally says 'cloud-masked composite \u00b130 days' regardless of
    what actually happened silently overclaims when the window had to widen
    or coverage stayed poor even after widening — this lets the caller be
    honest about which happened instead."""
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

        thumb_bytes = _fetch_thumb_bytes(
            composite, region,
            {"bands": ["B4", "B3", "B2"], "min": 0, "max": 0.3, "gamma": 1.3},
        )
        if thumb_bytes is None:
            continue
        return {"bytes": thumb_bytes, "window_days": window, "valid_pct": round(valid_frac * 100, 1)}
    return None


def get_optical_thumbnail(aoi: Dict[str, Any], center_date: str, days_window: int = 30) -> Optional[bytes]:
    """Sentinel-2 true-color (B4/B3/B2) cloud-masked median composite,
    centered on center_date. Used for most analysis types — the imagery a
    person would recognize as "what the satellite actually saw".

    Thin wrapper over get_optical_thumbnail_with_coverage() (bytes only,
    for callers that don't need the window/coverage metadata) — kept
    separate rather than duplicating the widening/stretch logic so the two
    can't drift out of sync."""
    result = get_optical_thumbnail_with_coverage(aoi, center_date, days_window)
    return result["bytes"] if result else None


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


def get_lst_thumbnail(aoi: Dict[str, Any], center_date: str, days_window: int = 90) -> Optional[bytes]:
    """Colored Landsat thermal (LST) map — the surface the temperature
    context reading is computed from. Blue = cooler, red = hotter."""
    from datetime import datetime as _dt
    region = _polygon_geometry(aoi)
    center_dt = _dt.strptime(center_date, "%Y-%m-%d")
    start = ee.Date((center_dt - timedelta(days=days_window)).strftime("%Y-%m-%d"))
    end = _cap_end_date(center_date)

    def _collection(coll_id):
        return (
            ee.ImageCollection(coll_id)
            .filterBounds(region)
            .filterDate(start, end)
            .filter(ee.Filter.lt("CLOUD_COVER", 20))
        )

    col = _collection("LANDSAT/LC08/C02/T1_L2")
    if col.size().getInfo() == 0:
        col = _collection("LANDSAT/LC09/C02/T1_L2")
    if col.size().getInfo() == 0:
        return None

    lst = col.map(
        lambda img: img.select("ST_B10").multiply(0.00341802).add(149.0).subtract(273.15).rename("LST_C")
    ).mean().clip(region)
    return _fetch_thumb_bytes(lst, region, {
        "min": 0, "max": 45,
        "palette": ["#1a4d7a", "#4a9ec9", "#e8e88a", "#d97a41", "#8b2020"],
    })


def get_precipitation_thumbnail(aoi: Dict[str, Any], center_date: str, days_window: int = 90) -> Optional[bytes]:
    """Colored CHIRPS rainfall-accumulation map over the window. Brown = dry
    (little accumulated rainfall), blue = wet (high accumulated rainfall)."""
    from datetime import datetime as _dt
    region = _polygon_geometry(aoi)
    center_dt = _dt.strptime(center_date, "%Y-%m-%d")
    start = (center_dt - timedelta(days=days_window)).strftime("%Y-%m-%d")
    end = center_dt.strftime("%Y-%m-%d")

    col = (
        ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
        .filterBounds(region)
        .filterDate(start, end)
    )
    if col.size().getInfo() == 0:
        return None
    total = col.sum().clip(region)
    return _fetch_thumb_bytes(total, region, {
        "min": 0, "max": 400,
        "palette": ["#8b6b3d", "#c9a86a", "#e8e88a", "#4a9ec9", "#1a4d7a"],
    })


def get_groundwater_thumbnail(aoi: Dict[str, Any], center_date: str) -> Optional[bytes]:
    """Colored GRACE terrestrial-water-storage-anomaly map — coarse
    (~300km grid), so this shows the regional trend context, not
    parcel-level detail. Brown = depleted anomaly, blue = surplus anomaly."""
    from datetime import datetime as _dt
    region = _polygon_geometry(aoi)
    center_dt = _dt.strptime(center_date, "%Y-%m-%d")
    start = (center_dt - timedelta(days=180)).strftime("%Y-%m-%d")
    end = center_dt.strftime("%Y-%m-%d")

    col = (
        ee.ImageCollection("NASA/GRACE/MASS_GRIDS_V04/LAND")
        .filterBounds(region)
        .filterDate(start, end)
        .select("lwe_thickness_csr")
    )
    if col.size().getInfo() == 0:
        return None
    latest = col.sort("system:time_start", False).first().clip(region)
    return _fetch_thumb_bytes(latest, region, {
        "min": -20, "max": 20,
        "palette": ["#8b6b3d", "#c9a86a", "#e8e88a", "#4a9ec9", "#1a4d7a"],
    })


def get_thumbnail_for_analysis(analysis_type: str, aoi: Dict[str, Any], date: str) -> Optional[bytes]:
    if analysis_type == "flood_detection":
        return get_sar_thumbnail(aoi, date)
    return get_optical_thumbnail(aoi, date)


def _s2_composite(region: ee.Geometry, center_date: str, days_window: int) -> Optional[ee.Image]:
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
    return col.median().clip(region)


def get_ndvi_thumbnail(aoi: Dict[str, Any], center_date: str, days_window: int = 30) -> Optional[bytes]:
    """Colored NDVI map — the actual vegetation-health surface the risk
    score's vegetation-loss sub-score is computed from, not just a generic
    photo. Red/orange = sparse or stressed vegetation, green = healthy."""
    region = _polygon_geometry(aoi)
    composite = _s2_composite(region, center_date, days_window)
    if composite is None:
        return None
    ndvi = composite.normalizedDifference(["B8", "B4"])
    return _fetch_thumb_bytes(ndvi, region, {
        "min": -0.2, "max": 0.8,
        "palette": ["#a83232", "#d9a441", "#e8e88a", "#8fd453", "#1a7a1a"],
    })


def get_nddi_thumbnail(aoi: Dict[str, Any], center_date: str, days_window: int = 30) -> Optional[bytes]:
    """Colored NDDI drought map — the surface the drought sub-score is
    computed from. Green/blue = moist, red/brown = drought-stressed."""
    region = _polygon_geometry(aoi)
    composite = _s2_composite(region, center_date, days_window)
    if composite is None:
        return None
    ndvi = composite.normalizedDifference(["B8", "B4"]).rename("NDVI")
    ndwi = composite.normalizedDifference(["B3", "B8"]).rename("NDWI")
    nddi = ndvi.subtract(ndwi).divide(ndvi.add(ndwi))
    return _fetch_thumb_bytes(nddi, region, {
        "min": -0.5, "max": 1.0,
        "palette": ["#1a4d7a", "#4a9ec9", "#e8e88a", "#d97a41", "#8b2020"],
    })


def get_soil_moisture_thumbnail(aoi: Dict[str, Any], center_date: str, days_window: int = 90) -> Optional[Dict[str, Any]]:
    """Colored SMAP soil-moisture map — the surface the moisture-deficit
    sub-score is computed from. Brown = dry, blue = moist. Uses NASA/SMAP/
    SPL4SMGP/008 (band sm_surface) — same dataset compute_soil_moisture in
    gee_client.py uses; the old NASA_USDA/HSL/SMAP10KM_soil_moisture is
    deprecated and appears to have stopped receiving new imagery.

    Uses a DYNAMIC min/max stretch computed from the actual observed values
    in this AOI, rather than a fixed 0-0.5 range. A fixed wide stretch can
    make a real, valid soil-moisture map look like one flat color when the
    AOI's true value range is narrow (e.g. a large, uniformly dry region in
    the dry season genuinely might only span ~0.05-0.15 m3/m3 — squashed
    into a 0-0.5 scale, that whole range renders as barely-distinguishable
    shades near the 'dry' end of the palette, which is what a flat-looking
    SMAP thumbnail usually means: real but narrow variation, not a bug or
    missing data). Returns the actual stretch bounds used so the caller can
    build a legend that matches what's actually shown, instead of a
    generic 0-0.5 legend that wouldn't reflect the image."""
    region = _polygon_geometry(aoi)
    center_dt = datetime.strptime(center_date, "%Y-%m-%d")
    start = ee.Date((center_dt - timedelta(days=days_window)).strftime("%Y-%m-%d"))
    end = _cap_end_date((center_dt + timedelta(days=days_window)).strftime("%Y-%m-%d"))
    col = (
        ee.ImageCollection("NASA/SMAP/SPL4SMGP/008")
        .filterBounds(region)
        .filterDate(start, end)
        .select("sm_surface")
    )
    if col.size().getInfo() == 0:
        return None
    composite = col.mean().clip(region)
    palette = ["#8b6b3d", "#c9a86a", "#a8c9d4", "#4a9ec9", "#1a4d7a"]

    # 2nd/98th percentile rather than raw min/max, so a couple of outlier
    # pixels (sensor noise, coastline/water-body edge effects) can't blow
    # the stretch out and flatten the *real* signal right back out again.
    #
    # This stats step is a separate, ADDITIONAL reduceRegion on top of what
    # the thumbnail generation itself already needs — SMAP L4 is 3-hourly,
    # so a +/-90-day window is up to ~1440 images to average first. Kept
    # deliberately cheap (coarse scale, a short recent sub-window rather
    # than the full heavy composite) so this extra step can't become the
    # reason the whole thumbnail fails to render on a large/complex AOI —
    # it only needs to be a good-enough estimate of the value range, not a
    # precise one.
    vmin, vmax = 0.0, 0.5
    try:
        stats_window_start = _cap_end_date((center_dt - timedelta(days=14)).strftime("%Y-%m-%d"))
        stats_composite = (
            ee.ImageCollection("NASA/SMAP/SPL4SMGP/008")
            .filterBounds(region)
            .filterDate(stats_window_start, end)
            .select("sm_surface")
            .mean()
        )
        stats = stats_composite.reduceRegion(
            reducer=ee.Reducer.percentile([2, 98]), geometry=region, scale=50000,
            maxPixels=1e9, bestEffort=True, tileScale=8,
        ).getInfo()
        p2 = stats.get("sm_surface_p2")
        p98 = stats.get("sm_surface_p98")
        if p2 is not None and p98 is not None and float(p98) > float(p2):
            vmin, vmax = float(p2), float(p98)
    except Exception as e:
        logger.warning(f"soil moisture dynamic stretch failed, using fixed 0-0.5 range: {e}")

    thumb_bytes = _fetch_thumb_bytes(composite, region, {"min": vmin, "max": vmax, "palette": palette})
    if thumb_bytes is None and (vmin, vmax) != (0.0, 0.5):
        # The dynamic-range attempt failed for some reason unrelated to the
        # stats step (e.g. a transient fetch error) — retry once with the
        # plain fixed range rather than giving up on the whole image.
        logger.warning("soil moisture thumbnail fetch failed with dynamic range, retrying with fixed 0-0.5 range")
        vmin, vmax = 0.0, 0.5
        thumb_bytes = _fetch_thumb_bytes(composite, region, {"min": vmin, "max": vmax, "palette": palette})
    if thumb_bytes is None:
        return None
    return {"bytes": thumb_bytes, "min": round(vmin, 4), "max": round(vmax, 4), "palette": palette}
