"""
gee_remote_sensing.py — a direct-access remote sensing toolkit,
separate from the 9 natural-language-driven metrics in gee_client.py
(vegetation_change, flood_detection, etc.). Those answer "did X change
between two dates" for a fixed set of questions; this exposes the
underlying spectral/radar/terrain building blocks directly — the kind
of raw access a remote sensing researcher (IIRS/NRSC/ISRO-adjacent
work) actually wants: pick an index, a date range, an AOI, get real
numbers with the method stated, not a pre-packaged narrative.

Reuses gee_client.py's helpers (_polygon_geometry, _validate_date_range,
_calc_area_km2, _region_area_km2, cloud masking) rather than
duplicating them, so AOI parsing/validation behaves identically to the
rest of the app.

Every function here follows the same rule the flood-detection function
in gee_client.py already sets: state the method, the dataset, and the
resolution in the docstring and in the returned "method" field, so the
result is auditable by someone who knows what they're looking at —
never just a bare number.
"""

import logging
import math
from typing import Dict, Any, Optional

import ee

from .gee_client import (
    _polygon_geometry, _validate_date_range, _require_start_after,
    _cap_end_date, _mask_s2_clouds, _region_area_km2, _calc_area_km2,
)
from .satellite_imagery import _fetch_thumb_bytes

logger = logging.getLogger(__name__)


def _tile_layer(image: "ee.Image", vis_params: Dict[str, Any]) -> Dict[str, Any]:
    """Returns an XYZ tile URL template (e.g. '.../{z}/{x}/{y}') for the
    given image, so the frontend can render the ACTUAL pixel-level raster
    as a Leaflet tile layer on the live map — not just the AOI-averaged
    number every tool already returns. This is a live GEE-backed tile
    endpoint, not a static thumbnail: panning/zooming re-requests tiles
    from Earth Engine directly, same mechanism the existing satellite
    layer toggles in App.jsx already use for NDVI/SAR/Thermal.

    vis_params follows GEE's normal visualization param shape, e.g.
    {'min': -1, 'max': 1, 'palette': ['red', 'white', 'green']}.
    """
    map_id_dict = image.getMapId(vis_params)
    return {
        "tile_url": map_id_dict["tile_fetcher"].url_format,
        "vis_params": vis_params,
    }


# GeoTIFF export cap. getDownloadURL() builds the file synchronously in
# the request (no batch task / polling needed, unlike ee.batch.Export),
# but that only works for reasonably small rasters — GEE's own hard
# limit on this call is ~32MB, and past a few thousand pixels per side
# things get slow even well under that. 5000x5000 at typical Sentinel-2
# 10-20m scale already covers a fairly large AOI; anything bigger should
# use ee.batch.Export to Drive/GCS instead, which isn't implemented here.
_MAX_EXPORT_PIXELS_PER_SIDE = 5000


def _download_url(image: "ee.Image", region: "ee.Geometry", scale: int) -> Optional[str]:
    """GeoTIFF download URL for `image`, clipped to `region` at `scale`
    meters/pixel — the raw pixel data behind whatever AOI-mean number or
    map_layer the caller already returned, so it can be pulled into
    QGIS/SNAP/Python and checked independently rather than taken on
    trust. Returns None (rather than raising) if the AOI is too large
    for a synchronous export at the given scale, since every tool that
    calls this already has a real result to return either way — a
    failed export shouldn't fail the whole request.
    """
    try:
        return image.getDownloadURL({
            "region": region, "scale": scale, "format": "GEO_TIFF",
            "maxPixels": _MAX_EXPORT_PIXELS_PER_SIDE * _MAX_EXPORT_PIXELS_PER_SIDE,
        })
    except Exception as e:
        logger.warning(f"GeoTIFF export URL failed (AOI likely too large at this scale): {type(e).__name__}: {e}")
        return None


# ═════════════════════════════════════════════════════════════════════════════
# Spectral index formulas — all standard, citable definitions. Computed on a
# cloud-masked Sentinel-2 SR median composite (same cloud-masking as the rest
# of this project — see _mask_s2_clouds in gee_client.py).
# ═════════════════════════════════════════════════════════════════════════════

INDEX_DEFINITIONS = {
    "ndvi": {
        "label": "NDVI — Normalized Difference Vegetation Index",
        "formula": "(NIR - Red) / (NIR + Red)  =  (B8 - B4) / (B8 + B4)",
        "citation": "Rouse et al. 1974",
        "bands": ["B8", "B4"],
        "interpretation": "Higher = denser/healthier green vegetation. <0 typically water/cloud/snow, 0-0.2 bare soil/sparse veg, 0.2-0.5 moderate, >0.5 dense vegetation.",
    },
    "ndwi": {
        "label": "NDWI — Normalized Difference Water Index (McFeeters)",
        "formula": "(Green - NIR) / (Green + NIR)  =  (B3 - B8) / (B3 + B8)",
        "citation": "McFeeters 1996",
        "bands": ["B3", "B8"],
        "interpretation": "Higher = more open water. Tuned for open water bodies; can under-detect water in built-up areas — use MNDWI there instead.",
    },
    "mndwi": {
        "label": "MNDWI — Modified NDWI",
        "formula": "(Green - SWIR1) / (Green + SWIR1)  =  (B3 - B11) / (B3 + B11)",
        "citation": "Xu 2006",
        "bands": ["B3", "B11"],
        "interpretation": "Higher = more water. Better than NDWI at suppressing built-up/soil noise, standard choice for urban or mixed-landuse water mapping.",
    },
    "ndbi": {
        "label": "NDBI — Normalized Difference Built-up Index",
        "formula": "(SWIR1 - NIR) / (SWIR1 + NIR)  =  (B11 - B8) / (B11 + B8)",
        "citation": "Zha et al. 2003",
        "bands": ["B11", "B8"],
        "interpretation": "Higher = more built-up/impervious surface. Bare soil can also read positive — cross-check against NDVI to separate the two.",
    },
    "savi": {
        "label": "SAVI — Soil-Adjusted Vegetation Index",
        "formula": "((NIR - Red) / (NIR + Red + L)) x (1 + L), L = 0.5",
        "citation": "Huete 1988",
        "bands": ["B8", "B4"],
        "interpretation": "NDVI corrected for soil brightness — preferred over NDVI in sparse-vegetation/arid AOIs where bare soil otherwise skews the index.",
    },
    "evi": {
        "label": "EVI — Enhanced Vegetation Index",
        "formula": "2.5 x (NIR - Red) / (NIR + 6xRed - 7.5xBlue + 1)",
        "citation": "Huete et al. 2002 (MODIS EVI algorithm)",
        "bands": ["B8", "B4", "B2"],
        "interpretation": "Corrects for atmospheric haze and canopy background — more reliable than NDVI in high-biomass (dense canopy) areas where NDVI saturates.",
    },
    "ndsi": {
        "label": "NDSI — Normalized Difference Snow Index",
        "formula": "(Green - SWIR1) / (Green + SWIR1)  =  (B3 - B11) / (B3 + B11)",
        "citation": "Hall et al. 1995",
        "bands": ["B3", "B11"],
        "interpretation": "Same band math as MNDWI, different interpretation context: NDSI > 0.4 is the standard threshold for classifying a pixel as snow/ice-covered.",
    },
}


def _index_image(composite: ee.Image, index_id: str) -> ee.Image:
    d = INDEX_DEFINITIONS[index_id]
    if index_id == "savi":
        nir, red = composite.select("B8"), composite.select("B4")
        L = 0.5
        return nir.subtract(red).divide(nir.add(red).add(L)).multiply(1 + L).rename("SAVI")
    if index_id == "evi":
        nir, red, blue = composite.select("B8"), composite.select("B4"), composite.select("B2")
        return nir.subtract(red).multiply(2.5).divide(
            nir.add(red.multiply(6)).subtract(blue.multiply(7.5)).add(1)
        ).rename("EVI")
    # NDVI/NDWI/MNDWI/NDBI/NDSI are all a normalizedDifference of two bands —
    # GEE's built-in handles (a-b)/(a+b) directly rather than hand-rolling it.
    b1, b2 = d["bands"][0], d["bands"][1]
    return composite.normalizedDifference([b1, b2]).rename(index_id.upper())


def _s2_composite(region: ee.Geometry, start: str, end: str):
    _validate_date_range(start, end)
    end_ee = _cap_end_date(end)
    col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(region)
        .filterDate(ee.Date(start), end_ee)
        .map(_mask_s2_clouds)
    )
    count = col.size().getInfo()
    if count == 0:
        raise ValueError(f"No cloud-free Sentinel-2 imagery found for {start} - {end}. Try a wider date range.")
    return col.median(), count


def compute_spectral_indices(aoi: Dict, start_date: str, end_date: str, indices: list = None) -> Dict[str, Any]:
    """Computes one or more spectral indices (see INDEX_DEFINITIONS) as
    AOI-mean values from a cloud-masked Sentinel-2 SR median composite.
    Default: all 7 indices. 10-20m native resolution depending on band
    (B4/B3/B2/B8 are 10m, B11 is 20m)."""
    logger.info(f"GEE (remote sensing): spectral_indices {indices} {start_date} -> {end_date}")
    indices = indices or list(INDEX_DEFINITIONS.keys())
    unknown = [i for i in indices if i not in INDEX_DEFINITIONS]
    if unknown:
        raise ValueError(f"Unknown index/indices: {unknown}. Valid: {list(INDEX_DEFINITIONS)}")

    region = _polygon_geometry(aoi)
    composite, scene_count = _s2_composite(region, start_date, end_date)

    # Fraction of the AOI actually covered by cloud-free pixels in the
    # composite (vs. gaps left by cloud masking) — an AOI-mean number
    # computed from a composite that's only 40% valid pixel coverage is
    # a much shakier number than one from 95% coverage, but without this
    # field there'd be no way to tell the two apart.
    valid_px = composite.select("B4").mask().reduceRegion(
        reducer=ee.Reducer.mean(), geometry=region, scale=20, maxPixels=1e9, bestEffort=True, tileScale=4,
    ).getInfo()
    valid_pixel_fraction = round((valid_px.get("B4", 0) or 0), 4)

    results = {}
    for idx_id in indices:
        img = _index_image(composite, idx_id)
        band_name = idx_id.upper()
        stats = img.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.minMax(), sharedInputs=True).combine(ee.Reducer.stdDev(), sharedInputs=True),
            geometry=region, scale=20, maxPixels=1e9, bestEffort=True, tileScale=4,
        ).getInfo()
        d = INDEX_DEFINITIONS[idx_id]
        results[idx_id] = {
            "label": d["label"], "formula": d["formula"], "citation": d["citation"],
            "interpretation": d["interpretation"],
            "mean": round(stats.get(f"{band_name}_mean", 0) or 0, 4),
            "min": round(stats.get(f"{band_name}_min", 0) or 0, 4),
            "max": round(stats.get(f"{band_name}_max", 0) or 0, 4),
            "std_dev": round(stats.get(f"{band_name}_stdDev", 0) or 0, 4),
            "map_layer": _tile_layer(img, INDEX_PALETTES.get(idx_id, {"min": -1, "max": 1, "palette": ["#000000", "#ffffff"]})),
            "download_url": _download_url(img, region, scale=20),
        }

    return {
        "indices": results,
        "method": f"Sentinel-2 SR Harmonized, cloud-masked via Scene Classification Layer, median composite of {scene_count} scene(s), 10-20m native resolution.",
        "scene_count": scene_count,
        "valid_pixel_fraction": valid_pixel_fraction,
        "aoi_area_km2": round(_region_area_km2(region), 3),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Terrain analysis — Copernicus DEM GLO-30 (30m global DSM, TanDEM-X derived)
# ═════════════════════════════════════════════════════════════════════════════

def compute_terrain_analysis(aoi: Dict) -> Dict[str, Any]:
    """Elevation/slope/aspect statistics from the Copernicus DEM GLO-30
    (30m global Digital Surface Model, TanDEM-X-derived — see
    COPERNICUS/DEM/GLO30 in the GEE catalog). No date range: this is a
    single static global DEM, not a time series."""
    logger.info("GEE (remote sensing): terrain_analysis")
    region = _polygon_geometry(aoi)

    dem = ee.ImageCollection("COPERNICUS/DEM/GLO30").select("DEM").mosaic()
    slope = ee.Terrain.slope(dem)     # degrees
    aspect = ee.Terrain.aspect(dem)   # degrees, 0=N, 90=E, 180=S, 270=W

    elev_stats = dem.reduceRegion(
        reducer=ee.Reducer.mean().combine(ee.Reducer.minMax(), sharedInputs=True).combine(ee.Reducer.stdDev(), sharedInputs=True),
        geometry=region, scale=30, maxPixels=1e9, bestEffort=True, tileScale=4,
    ).getInfo()
    slope_stats = slope.reduceRegion(
        reducer=ee.Reducer.mean().combine(ee.Reducer.minMax(), sharedInputs=True),
        geometry=region, scale=30, maxPixels=1e9, bestEffort=True, tileScale=4,
    ).getInfo()

    # Aspect is circular (0deg == 360deg) — a plain mean is meaningless
    # (mean of 350 and 10 should be ~0/360, not 180). Compute the
    # circular mean via unit vectors instead.
    aspect_rad = aspect.multiply(3.14159265 / 180)
    sin_mean = aspect_rad.sin().reduceRegion(reducer=ee.Reducer.mean(), geometry=region, scale=30, maxPixels=1e9, bestEffort=True, tileScale=4).getInfo()
    cos_mean = aspect_rad.cos().reduceRegion(reducer=ee.Reducer.mean(), geometry=region, scale=30, maxPixels=1e9, bestEffort=True, tileScale=4).getInfo()
    sin_v = sin_mean.get("aspect", 0) or 0
    cos_v = cos_mean.get("aspect", 0) or 0
    circular_mean_aspect = (math.degrees(math.atan2(sin_v, cos_v)) + 360) % 360

    def _compass(deg):
        dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        return dirs[round(deg / 45) % 8]

    return {
        "elevation_m": {
            "mean": round(elev_stats.get("DEM_mean", 0) or 0, 1),
            "min": round(elev_stats.get("DEM_min", 0) or 0, 1),
            "max": round(elev_stats.get("DEM_max", 0) or 0, 1),
            "std_dev": round(elev_stats.get("DEM_stdDev", 0) or 0, 1),
        },
        "slope_degrees": {
            "mean": round(slope_stats.get("slope_mean", 0) or 0, 2),
            "min": round(slope_stats.get("slope_min", 0) or 0, 2),
            "max": round(slope_stats.get("slope_max", 0) or 0, 2),
        },
        "dominant_aspect": {"degrees": round(circular_mean_aspect, 1), "compass": _compass(circular_mean_aspect)},
        "vertical_datum": "EGM2008 (EPSG:3855) — a 0m reading here does NOT mean mean sea level; see COPERNICUS/DEM/GLO30 documentation.",
        # Hillshade rather than raw elevation as the map layer — a flat
        # elevation color ramp needs a per-AOI min/max to look like
        # anything, while a hillshade reads as actual terrain relief at
        # a glance regardless of the AOI's absolute elevation range.
        "map_layer": _tile_layer(ee.Terrain.hillshade(dem), {"min": 0, "max": 255}),
        # Export the raw DEM, not the hillshade — hillshade is a display
        # rendering, the DEM itself is the actual data someone would want
        # to pull into QGIS/SNAP.
        "download_url": _download_url(dem, region, scale=30),
        "method": "Copernicus DEM GLO-30 (30m, TanDEM-X-derived Digital Surface Model incl. buildings/vegetation, not bare-earth). Slope/aspect via ee.Terrain. Aspect averaged circularly (unit-vector mean), not arithmetically.",
        "aoi_area_km2": round(_region_area_km2(region), 3),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Land cover classification — ESA WorldCover v200 (10m, 2021, Sentinel-1+2-derived)
# ═════════════════════════════════════════════════════════════════════════════

# Standard, roughly perceptually-even palettes for the indices most
# likely to actually get looked at as a map rather than just a number —
# not exhaustive, but covers vegetation/water/built-up. Module-level
# (not per-function) since both the live map_layer and the PDF report's
# static thumbnail need the exact same visualization for the same index.
INDEX_PALETTES = {
    "ndvi": {"min": -0.2, "max": 0.8, "palette": ["#a50026", "#f46d43", "#fee08b", "#66bd63", "#1a9850"]},
    "savi": {"min": -0.2, "max": 0.8, "palette": ["#a50026", "#f46d43", "#fee08b", "#66bd63", "#1a9850"]},
    "evi": {"min": -0.2, "max": 0.8, "palette": ["#a50026", "#f46d43", "#fee08b", "#66bd63", "#1a9850"]},
    "ndwi": {"min": -0.5, "max": 0.5, "palette": ["#8c510a", "#f6e8c3", "#c7eae5", "#01665e"]},
    "mndwi": {"min": -0.5, "max": 0.5, "palette": ["#8c510a", "#f6e8c3", "#c7eae5", "#01665e"]},
    "ndbi": {"min": -0.3, "max": 0.3, "palette": ["#ffffcc", "#fed976", "#fd8d3c", "#e31a1c"]},
    "ndsi": {"min": -0.2, "max": 0.8, "palette": ["#08306b", "#4292c6", "#c6dbef", "#ffffff"]},
}

WORLDCOVER_CLASSES = {
    10: "Tree cover", 20: "Shrubland", 30: "Grassland", 40: "Cropland",
    50: "Built-up", 60: "Bare / sparse vegetation", 70: "Snow and ice",
    80: "Permanent water bodies", 90: "Herbaceous wetland", 95: "Mangroves",
    100: "Moss and lichen",
}

# ESA WorldCover's own official per-class colors (from the product's
# published color legend) — using these rather than inventing our own
# palette means the map matches what WorldCover's own documentation and
# other tools that display this dataset show, so it's recognizable to
# anyone already familiar with the product.
WORLDCOVER_PALETTE = [
    "006400", "ffbb22", "ffff4c", "f096ff", "fa0000", "b4b4b4",
    "f0f0f0", "0064c8", "0096a0", "00cf75", "fae6a0",
]  # ordered to match class codes 10,20,...,100


def compute_lulc_classification(aoi: Dict) -> Dict[str, Any]:
    """Land-use/land-cover class area breakdown from ESA WorldCover v200
    (10m resolution, calendar year 2021, derived from Sentinel-1+2,
    76.7% overall validated accuracy per the ESA WorldCover Product
    Validation Report v2.0). Single fixed year (2021) — WorldCover does
    not currently have an updated vintage in the public GEE catalog as
    of this writing."""
    logger.info("GEE (remote sensing): lulc_classification")
    region = _polygon_geometry(aoi)

    wc = ee.ImageCollection("ESA/WorldCover/v200").first().select("Map")
    area_img = ee.Image.pixelArea().divide(1_000_000).addBands(wc)  # km2 per pixel + class

    hist = area_img.reduceRegion(
        reducer=ee.Reducer.sum().group(groupField=1, groupName="class"),
        geometry=region, scale=10, maxPixels=1e10, bestEffort=True, tileScale=4,
    ).getInfo()

    groups = hist.get("groups", [])
    total_km2 = sum(g["sum"] for g in groups) or 1
    classes = []
    for g in groups:
        code = int(g["class"])
        classes.append({
            "code": code,
            "label": WORLDCOVER_CLASSES.get(code, f"Unknown class {code}"),
            "area_km2": round(g["sum"], 3),
            "pct_of_aoi": round(g["sum"] / total_km2 * 100, 2),
        })
    classes.sort(key=lambda c: c["area_km2"], reverse=True)

    # WorldCover's class codes (10, 20, ..., 100) aren't contiguous, so
    # visualizing them directly would have GEE linearly interpolate the
    # palette across the whole 10-100 range — smearing color between
    # classes and through the unused code gaps (e.g. between 40 and 50)
    # instead of giving each class one flat, correct color. Remap to a
    # dense 0..10 sequence first so each class lands on an exact
    # palette entry.
    class_codes = sorted(WORLDCOVER_CLASSES.keys())
    wc_dense = wc.remap(class_codes, list(range(len(class_codes))))

    return {
        "classes": classes,
        "total_classified_km2": round(total_km2, 3),
        "aoi_area_km2": round(_region_area_km2(region), 3),
        "map_layer": _tile_layer(wc_dense, {
            "min": 0, "max": len(class_codes) - 1,
            "palette": WORLDCOVER_PALETTE,
        }),
        # Export the ORIGINAL class-code image (wc), not the display-only
        # dense-remapped one (wc_dense) — someone pulling this into QGIS
        # wants WorldCover's real class codes (10=Tree cover, etc., per
        # WORLDCOVER_CLASSES above), not our internal 0-10 display index.
        "download_url": _download_url(wc, region, scale=10),
        "method": "ESA WorldCover v200, 10m resolution, calendar year 2021, Sentinel-1+Sentinel-2-derived (Zanaga et al. 2022, doi:10.5281/zenodo.7254221). Overall validated accuracy 76.7%. Map colors are WorldCover's own official class palette.",
    }


# ═════════════════════════════════════════════════════════════════════════════
# Snow cover — NDSI via Sentinel-2, standard Hall et al. 1995 threshold.
# ═════════════════════════════════════════════════════════════════════════════

NDSI_SNOW_THRESHOLD = 0.4  # standard classification threshold, Hall et al. 1995


def compute_snow_cover(aoi: Dict, start_date: str, end_date: str) -> Dict[str, Any]:
    """Snow/ice-covered area and NDSI statistics from a cloud-masked
    Sentinel-2 SR composite. Classification uses the standard NDSI >
    0.4 threshold (Hall et al. 1995) — the same threshold MODIS's own
    operational snow product uses, applied here at Sentinel-2's much
    finer 20m resolution (vs. MODIS's 500m)."""
    logger.info(f"GEE (remote sensing): snow_cover {start_date} -> {end_date}")
    region = _polygon_geometry(aoi)
    composite, scene_count = _s2_composite(region, start_date, end_date)

    ndsi = composite.normalizedDifference(["B3", "B11"]).rename("NDSI")
    snow_mask = ndsi.gt(NDSI_SNOW_THRESHOLD)

    snow_km2 = _calc_area_km2(snow_mask, region, scale=20)
    aoi_km2 = _region_area_km2(region)

    ndsi_stats = ndsi.reduceRegion(
        reducer=ee.Reducer.mean().combine(ee.Reducer.minMax(), sharedInputs=True).combine(ee.Reducer.stdDev(), sharedInputs=True),
        geometry=region, scale=20, maxPixels=1e9, bestEffort=True, tileScale=4,
    ).getInfo()

    valid_px = composite.select("B3").mask().reduceRegion(
        reducer=ee.Reducer.mean(), geometry=region, scale=20, maxPixels=1e9, bestEffort=True, tileScale=4,
    ).getInfo()
    valid_pixel_fraction = round((valid_px.get("B3", 0) or 0), 4)

    return {
        "snow_covered_km2": round(snow_km2, 3),
        "aoi_area_km2": round(aoi_km2, 3),
        "snow_cover_pct": round((snow_km2 / aoi_km2 * 100) if aoi_km2 > 0 else 0, 2),
        "ndsi_mean": round(ndsi_stats.get("NDSI_mean", 0) or 0, 4),
        "ndsi_max": round(ndsi_stats.get("NDSI_max", 0) or 0, 4),
        "ndsi_std_dev": round(ndsi_stats.get("NDSI_stdDev", 0) or 0, 4),
        "threshold_used": NDSI_SNOW_THRESHOLD,
        "valid_pixel_fraction": valid_pixel_fraction,
        # Binary mask (0/1) rather than the continuous NDSI field — a
        # scientist checking THIS tool's output wants to see exactly the
        # pixels classified as snow at the stated threshold, not a
        # smoothed gradient that hides where the cutoff actually fell.
        "map_layer": _tile_layer(snow_mask.selfMask(), {"palette": ["#4292c6"]}),
        # Export the continuous NDSI image, not the binary mask — the
        # mask is derived (NDSI > threshold), so exporting NDSI itself
        # lets someone re-apply a different threshold or check the
        # classification's sensitivity, which the mask alone can't do.
        "download_url": _download_url(ndsi, region, scale=20),
        "method": f"NDSI = (Green-SWIR1)/(Green+SWIR1) via Sentinel-2 SR (B3/B11), cloud-masked median composite of {scene_count} scene(s), 20m resolution. Classified as snow where NDSI > {NDSI_SNOW_THRESHOLD} (Hall et al. 1995 threshold).",
        "scene_count": scene_count,
    }


# ═════════════════════════════════════════════════════════════════════════════
# SAR backscatter — Sentinel-1 GRD, all-weather day/night radar. Complements
# gee_client.py's flood_detection (a pre/post SAR COMPARISON) with raw
# single-period backscatter statistics + a dual-pol vegetation index.
# ═════════════════════════════════════════════════════════════════════════════

def compute_sar_backscatter(aoi: Dict, start_date: str, end_date: str) -> Dict[str, Any]:
    """VV/VH backscatter statistics (dB) from Sentinel-1 GRD (IW mode),
    plus the dual-polarization Radar Vegetation Index (RVI). All-weather,
    day/night imaging — usable through cloud cover, unlike every other
    tool in this module."""
    logger.info(f"GEE (remote sensing): sar_backscatter {start_date} -> {end_date}")
    _validate_date_range(start_date, end_date)
    _require_start_after(start_date, "2014-04-01", "Sentinel-1")
    region = _polygon_geometry(aoi)
    end_ee = _cap_end_date(end_date)

    col = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(region)
        .filterDate(ee.Date(start_date), end_ee)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
    )
    scene_count = col.size().getInfo()
    if scene_count == 0:
        raise ValueError(f"No dual-pol (VV+VH) Sentinel-1 IW imagery found for {start_date} - {end_date}. Try a wider date range.")

    composite = col.select(["VV", "VH"]).median()  # values already in dB in GEE's S1_GRD product

    stats = composite.reduceRegion(
        reducer=ee.Reducer.mean().combine(ee.Reducer.stdDev(), sharedInputs=True),
        geometry=region, scale=20, maxPixels=1e9, bestEffort=True, tileScale=4,
    ).getInfo()

    # RVI must be computed in the LINEAR power domain, not on the dB
    # values directly — a ratio of decibel (logarithmic) values isn't
    # physically meaningful the way a ratio of the underlying linear
    # backscatter power is. Convert dB -> linear (10^(dB/10)) first.
    vv_lin = ee.Image(10).pow(composite.select("VV").divide(10))
    vh_lin = ee.Image(10).pow(composite.select("VH").divide(10))
    rvi = vh_lin.multiply(4).divide(vv_lin.add(vh_lin)).rename("RVI")
    rvi_stats = rvi.reduceRegion(reducer=ee.Reducer.mean(), geometry=region, scale=20, maxPixels=1e9, bestEffort=True, tileScale=4).getInfo()

    valid_px = composite.select("VV").mask().reduceRegion(
        reducer=ee.Reducer.mean(), geometry=region, scale=20, maxPixels=1e9, bestEffort=True, tileScale=4,
    ).getInfo()
    valid_pixel_fraction = round((valid_px.get("VV", 0) or 0), 4)

    return {
        "vv_db": {"mean": round(stats.get("VV_mean", 0) or 0, 2), "std_dev": round(stats.get("VV_stdDev", 0) or 0, 2)},
        "vh_db": {"mean": round(stats.get("VH_mean", 0) or 0, 2), "std_dev": round(stats.get("VH_stdDev", 0) or 0, 2)},
        "radar_vegetation_index": round(rvi_stats.get("RVI", 0) or 0, 4),
        "scene_count": scene_count,
        "valid_pixel_fraction": valid_pixel_fraction,
        # RVI (not raw VV/VH dB) as the map layer — it's the single-band
        # normalized quantity, so it colors meaningfully across the AOI;
        # VV/VH dB would need a per-scene min/max to look right and
        # doesn't have a widely agreed-on color convention the way RVI's
        # 0-1 range does.
        "map_layer": _tile_layer(rvi, {"min": 0, "max": 1, "palette": ["#f7fcf5", "#74c476", "#00441b"]}),
        # Export the VV+VH composite (both dB bands), not just RVI — RVI
        # is a derived ratio; the raw dual-pol backscatter is the more
        # useful artifact to hand someone checking the RVI computation
        # itself, or wanting to compute a different derived index.
        "download_url": _download_url(composite, region, scale=20),
        "method": (
            f"Sentinel-1 GRD, IW mode, dual-pol (VV+VH), median composite of {scene_count} scene(s), 20m analysis "
            f"resolution. Backscatter reported in dB as provided by the GEE S1_GRD product. RVI = 4*VH/(VV+VH), "
            f"computed on linear (not dB) backscatter power — a rough proxy for vegetation structure/roughness, "
            f"higher over dense canopy, lower over smooth/bare surfaces and open water."
        ),
        "aoi_area_km2": round(_region_area_km2(region), 3),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Change detection — generalized two-period diff for any spectral index above,
# instead of a separate single-purpose function per index (gee_client.py's
# vegetation_change/builtup_change/water_change already do this per-index for
# the Analyze tab's natural-language flow; this is the same underlying idea
# exposed directly for any of the 7 indices, picked explicitly rather than
# inferred from a sentence).
# ═════════════════════════════════════════════════════════════════════════════

def compute_change_detection(aoi: Dict, index_id: str, period1_start: str, period1_end: str,
                              period2_start: str, period2_end: str) -> Dict[str, Any]:
    """Diffs a chosen spectral index between two independent date ranges
    (e.g. two different years/seasons) over the same AOI. Returns the
    mean value for each period, the delta, and % change — not just a
    single-date snapshot."""
    logger.info(f"GEE (remote sensing): change_detection {index_id} {period1_start}->{period1_end} vs {period2_start}->{period2_end}")
    if index_id not in INDEX_DEFINITIONS:
        raise ValueError(f"Unknown index: {index_id}. Valid: {list(INDEX_DEFINITIONS)}")

    region = _polygon_geometry(aoi)
    composite1, count1 = _s2_composite(region, period1_start, period1_end)
    composite2, count2 = _s2_composite(region, period2_start, period2_end)

    img1 = _index_image(composite1, index_id)
    img2 = _index_image(composite2, index_id)
    band_name = index_id.upper()

    stats1 = img1.reduceRegion(
        reducer=ee.Reducer.mean().combine(ee.Reducer.stdDev(), sharedInputs=True),
        geometry=region, scale=20, maxPixels=1e9, bestEffort=True, tileScale=4).getInfo()
    stats2 = img2.reduceRegion(
        reducer=ee.Reducer.mean().combine(ee.Reducer.stdDev(), sharedInputs=True),
        geometry=region, scale=20, maxPixels=1e9, bestEffort=True, tileScale=4).getInfo()
    # BUG FIX: .combine() suffixes each sub-reducer's output band name
    # (e.g. "NDVI_mean", "NDVI_stdDev") — reduceRegion on a COMBINED
    # reducer never returns a bare "NDVI" key. The previous code looked
    # up stats1.get(band_name, 0) (bare "NDVI"), which never existed in
    # a combined-reducer result, so it silently fell back to the 0
    # default every time, for every index and every date range —
    # producing mean1=mean2=0, delta=0, for ALL requests regardless of
    # real underlying data. std_dev below was already correctly suffixed
    # (f"{band_name}_stdDev"); mean needed the same fix.
    mean1 = stats1.get(f"{band_name}_mean", 0) or 0
    mean2 = stats2.get(f"{band_name}_mean", 0) or 0
    delta = mean2 - mean1
    pct_change = (delta / abs(mean1) * 100) if mean1 != 0 else None

    delta_img = img2.subtract(img1).rename("delta")

    d = INDEX_DEFINITIONS[index_id]
    return {
        "index": index_id, "label": d["label"],
        "period1": {"start": period1_start, "end": period1_end, "mean": round(mean1, 4), "std_dev": round(stats1.get(f"{band_name}_stdDev", 0) or 0, 4), "scene_count": count1},
        "period2": {"start": period2_start, "end": period2_end, "mean": round(mean2, 4), "std_dev": round(stats2.get(f"{band_name}_stdDev", 0) or 0, 4), "scene_count": count2},
        "delta": round(delta, 4),
        "pct_change": round(pct_change, 2) if pct_change is not None else None,
        # Diverging red-white-green: negative delta (index decreased) reads
        # red, positive (increased) reads green — consistent direction
        # regardless of which index was picked, so it's readable without
        # re-deriving what "positive" means for NDVI vs NDBI each time.
        "map_layer": _tile_layer(delta_img, {"min": -0.3, "max": 0.3, "palette": ["#a50026", "#ffffbf", "#1a9850"]}),
        "download_url": _download_url(delta_img, region, scale=20),
        "method": f"{d['label']} computed independently for both periods from cloud-masked Sentinel-2 SR median composites, 20m, then differenced (period 2 minus period 1). A positive delta means the index increased.",
    }


# ═════════════════════════════════════════════════════════════════════════════
# Burn severity — dNBR (differenced Normalized Burn Ratio), the standard USGS
# post-fire assessment method (Key & Benson 2006), distinct from gee_client.py's
# fire_detection (MODIS active-fire/burned-area DETECTION) — this is severity
# CLASSIFICATION of an already-identified burn, at Sentinel-2's finer 20m.
# ═════════════════════════════════════════════════════════════════════════════

# USGS FIREMON standard dNBR severity breakpoints (Key & Benson 2006)
DNBR_SEVERITY_CLASSES = [
    (-1.0, -0.25, "High post-fire regrowth"),
    (-0.25, -0.1, "Low post-fire regrowth"),
    (-0.1, 0.1, "Unburned"),
    (0.1, 0.27, "Low severity"),
    (0.27, 0.44, "Moderate-low severity"),
    (0.44, 0.66, "Moderate-high severity"),
    (0.66, 1.3, "High severity"),
]


def _classify_dnbr(value: float) -> str:
    for lo, hi, label in DNBR_SEVERITY_CLASSES:
        if lo <= value < hi:
            return label
    return "Unclassified"


def compute_burn_severity(aoi: Dict, pre_start: str, pre_end: str, post_start: str, post_end: str) -> Dict[str, Any]:
    """dNBR burn severity between a pre-fire and post-fire period, using
    the standard USGS FIREMON classification (Key & Benson 2006).
    NBR = (NIR - SWIR2) / (NIR + SWIR2) = (B8 - B12) / (B8 + B12);
    dNBR = NBR_pre - NBR_post (a positive dNBR indicates burn severity,
    consistent with USGS convention)."""
    logger.info(f"GEE (remote sensing): burn_severity pre={pre_start}->{pre_end} post={post_start}->{post_end}")
    region = _polygon_geometry(aoi)
    pre_composite, pre_count = _s2_composite(region, pre_start, pre_end)
    post_composite, post_count = _s2_composite(region, post_start, post_end)

    def _nbr(img):
        return img.normalizedDifference(["B8", "B12"]).rename("NBR")

    nbr_pre = _nbr(pre_composite)
    nbr_post = _nbr(post_composite)
    dnbr = nbr_pre.subtract(nbr_post).rename("dNBR")

    dnbr_stats = dnbr.reduceRegion(
        reducer=ee.Reducer.mean().combine(ee.Reducer.minMax(), sharedInputs=True).combine(ee.Reducer.stdDev(), sharedInputs=True),
        geometry=region, scale=20, maxPixels=1e9, bestEffort=True, tileScale=4,
    ).getInfo()

    # Area breakdown by severity class
    area_km2 = ee.Image.pixelArea().divide(1_000_000)
    class_areas = {}
    for lo, hi, label in DNBR_SEVERITY_CLASSES:
        mask = dnbr.gte(lo).And(dnbr.lt(hi))
        km2 = _calc_area_km2(mask, region, scale=20)
        if km2 > 0:
            class_areas[label] = round(km2, 3)

    return {
        "dnbr_mean": round(dnbr_stats.get("dNBR_mean", 0) or 0, 4),
        "dnbr_min": round(dnbr_stats.get("dNBR_min", 0) or 0, 4),
        "dnbr_max": round(dnbr_stats.get("dNBR_max", 0) or 0, 4),
        "dnbr_std_dev": round(dnbr_stats.get("dNBR_stdDev", 0) or 0, 4),
        "overall_classification": _classify_dnbr(dnbr_stats.get("dNBR_mean", 0) or 0),
        "area_by_severity_km2": class_areas,
        "aoi_area_km2": round(_region_area_km2(region), 3),
        "pre_fire_scenes": pre_count, "post_fire_scenes": post_count,
        # USGS FIREMON's own conventional severity color ramp (blue-green
        # regrowth through yellow/orange/red severity) — same breakpoints
        # as DNBR_SEVERITY_CLASSES above, so the map and the tabulated
        # area-by-severity numbers describe the same classification.
        "map_layer": _tile_layer(dnbr, {
            "min": -0.25, "max": 0.66,
            "palette": ["#1a9850", "#66bd63", "#ffffbf", "#fdae61", "#f46d43", "#a50026"],
        }),
        "download_url": _download_url(dnbr, region, scale=20),
        "method": (
            "dNBR (Key & Benson 2006, USGS FIREMON standard) = NBR_pre - NBR_post, where NBR = (B8-B12)/(B8+B12) "
            "on cloud-masked Sentinel-2 SR composites, 20m. Severity classes are the standard USGS breakpoints, "
            "not a custom threshold. Requires the pre-fire and post-fire periods to be specified correctly by the "
            "caller — this function has no way to verify a fire actually occurred in the given window."
        ),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Atmospheric composition — Sentinel-5P (TROPOMI), free, no key. Complements
# the surface-focused tools above with a column-density air-quality read —
# genuinely different physical quantity (atmospheric trace gas column, not
# surface reflectance/backscatter), included here as a first-class RS tool
# rather than folded into the Weather tab's ground-station AQI overlay.
# ═════════════════════════════════════════════════════════════════════════════

S5P_MIN_DATE = "2018-06-28"  # dataset start for the OFFL L3 NO2/SO2/CO/AAI products used below


def compute_atmospheric_composition(aoi: Dict, start_date: str, end_date: str) -> Dict[str, Any]:
    """Tropospheric NO2, SO2, CO column density, and the absorbing
    aerosol index (AAI) from Sentinel-5P/TROPOMI OFFL L3 products —
    column densities (mol/m^2), not ground-level concentrations, and
    not directly comparable to a surface AQI monitor reading."""
    logger.info(f"GEE (remote sensing): atmospheric_composition {start_date} -> {end_date}")
    _validate_date_range(start_date, end_date)
    _require_start_after(start_date, S5P_MIN_DATE, "Sentinel-5P")
    region = _polygon_geometry(aoi)
    end_ee = _cap_end_date(end_date)

    def _mean_band(collection_id: str, band: str) -> Dict[str, Any]:
        col = ee.ImageCollection(collection_id).select(band).filterBounds(region).filterDate(ee.Date(start_date), end_ee)
        n = col.size().getInfo()
        if n == 0:
            return {"mean": None, "std_dev": None, "scene_count": 0}
        stats = col.mean().reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.stdDev(), sharedInputs=True),
            geometry=region, scale=1113, maxPixels=1e9, bestEffort=True, tileScale=4,
        ).getInfo()
        return {"mean": stats.get(band), "std_dev": stats.get(f"{band}_stdDev"), "scene_count": n}

    no2 = _mean_band("COPERNICUS/S5P/OFFL/L3_NO2", "tropospheric_NO2_column_number_density")
    so2 = _mean_band("COPERNICUS/S5P/OFFL/L3_SO2", "SO2_column_number_density")
    co = _mean_band("COPERNICUS/S5P/OFFL/L3_CO", "CO_column_number_density")
    aai = _mean_band("COPERNICUS/S5P/OFFL/L3_NO2", "absorbing_aerosol_index")  # AAI ships as a band of the NO2 product

    def _fmt(d, unit, decimals=6):
        return {
            **d,
            "mean": round(d["mean"], decimals) if d["mean"] is not None else None,
            "std_dev": round(d["std_dev"], decimals) if d.get("std_dev") is not None else None,
            "unit": unit,
        }

    return {
        "no2": _fmt(no2, "mol/m^2 (tropospheric column)"),
        "so2": _fmt(so2, "mol/m^2 (total column)"),
        "co": _fmt(co, "mol/m^2 (total column)"),
        "aerosol_index": _fmt(aai, "dimensionless (UV Absorbing Aerosol Index)", decimals=3),
        "aoi_area_km2": round(_region_area_km2(region), 3),
        "method": (
            "Sentinel-5P/TROPOMI OFFL L3 products (NO2, SO2, CO, aerosol index), mean over the date range, "
            "~1.1km native resolution (reported at ~1113m). These are ATMOSPHERIC COLUMN densities integrated "
            "through the full air column (or troposphere, for NO2) — not ground-level concentrations, and not "
            "directly comparable in units to a surface AQI monitor (e.g. the Weather tab's CPCB stations)."
        ),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Index time series — the charting counterpart to Business Intelligence's
# Analysis tab. A single spectral_indices call gives one mean value for the
# whole date range; this breaks the range into sub-periods and computes the
# index for each one independently, so a trend (phenology, gradual
# degradation, seasonal cycle) is visible rather than collapsed into a
# single number.
# ═════════════════════════════════════════════════════════════════════════════

def compute_index_time_series(aoi: Dict, index_id: str, start_date: str, end_date: str, interval: str = "month") -> Dict[str, Any]:
    """Computes `index_id` independently for each sub-period between
    start_date and end_date (interval: 'month' or 'quarter'). Periods
    with zero cloud-free Sentinel-2 scenes are reported as gaps
    (value=None) rather than silently dropped or erroring the whole
    series — a gap is itself informative (persistent cloud cover)."""
    logger.info(f"GEE (remote sensing): index_time_series {index_id} {start_date} -> {end_date} ({interval})")
    if index_id not in INDEX_DEFINITIONS:
        raise ValueError(f"Unknown index: {index_id}. Valid: {list(INDEX_DEFINITIONS)}")
    if interval not in ("month", "quarter"):
        raise ValueError("interval must be 'month' or 'quarter'.")
    _validate_date_range(start_date, end_date)

    region = _polygon_geometry(aoi)
    step_months = 1 if interval == "month" else 3

    from datetime import date
    y, m = int(start_date[:4]), int(start_date[5:7])
    end_y, end_m = int(end_date[:4]), int(end_date[5:7])
    periods = []
    while (y, m) <= (end_y, end_m):
        p_start = date(y, m, 1)
        nm, ny = m + step_months, y
        if nm > 12:
            ny += (nm - 1) // 12
            nm = ((nm - 1) % 12) + 1
        p_end = date(ny, nm, 1)
        periods.append((p_start.isoformat(), p_end.isoformat()))
        y, m = ny, nm
        if len(periods) > 60:  # sanity cap — a multi-decade daily/monthly request shouldn't run unbounded
            break

    points = []
    band_name = index_id.upper()
    for p_start, p_end in periods:
        try:
            col = (
                ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                .filterBounds(region).filterDate(ee.Date(p_start), ee.Date(p_end))
                .map(_mask_s2_clouds)
            )
            n = col.size().getInfo()
            if n == 0:
                points.append({"date": p_start, "value": None, "scene_count": 0})
                continue
            img = _index_image(col.median(), index_id)
            stats = img.reduceRegion(reducer=ee.Reducer.mean(), geometry=region, scale=20, maxPixels=1e9, bestEffort=True, tileScale=4).getInfo()
            points.append({"date": p_start, "value": round(stats.get(band_name, 0) or 0, 4), "scene_count": n})
        except Exception as e:
            logger.warning(f"index_time_series: period {p_start}-{p_end} failed: {type(e).__name__}: {e}")
            points.append({"date": p_start, "value": None, "scene_count": 0})

    d = INDEX_DEFINITIONS[index_id]
    return {
        "index": index_id, "label": d["label"], "interval": interval,
        "points": points,
        "method": f"{d['label']} computed independently for each {interval} from a cloud-masked Sentinel-2 SR median composite, 20m. Periods with zero cloud-free scenes are reported as gaps (value: null), not interpolated or dropped.",
    }


def compute_land_surface_temperature(aoi: Dict, start_date: str, end_date: str) -> Dict[str, Any]:
    """Land Surface Temperature from Landsat 8/9 Collection 2 Level-2
    thermal band (ST_B10) — one of the most routinely used products in
    Indian remote sensing work (urban heat island studies, agricultural
    drought stress, pre-monsoon heat mapping), and a real gap in this
    toolkit until now: every other tool here is optical/SAR/atmospheric,
    none directly measured surface temperature.

    Uses the exact same scale/offset already vetted elsewhere in this
    codebase (satellite_imagery.py's get_lst_thumbnail, used in Analyze-
    tab PDF reports) — USGS's official Collection 2 Level-2 Surface
    Temperature conversion: DN * 0.00341802 + 149.0 = Kelvin.
    """
    logger.info(f"GEE (remote sensing): land_surface_temperature {start_date} -> {end_date}")
    _validate_date_range(start_date, end_date)
    _require_start_after(start_date, "2013-04-01", "Landsat 8")
    region = _polygon_geometry(aoi)
    end_ee = _cap_end_date(end_date)

    def _collection(coll_id):
        return (
            ee.ImageCollection(coll_id)
            .filterBounds(region).filterDate(ee.Date(start_date), end_ee)
            .filter(ee.Filter.lt("CLOUD_COVER", 20))
        )

    col = _collection("LANDSAT/LC08/C02/T1_L2")
    scene_count = col.size().getInfo()
    source = "Landsat 8"
    if scene_count == 0:
        col = _collection("LANDSAT/LC09/C02/T1_L2")
        scene_count = col.size().getInfo()
        source = "Landsat 9"
    if scene_count == 0:
        return {
            "lst_celsius": None, "scene_count": 0, "source": None,
            "method": (
                "Landsat 8/9 Collection 2 Level-2 thermal (ST_B10), <20% scene cloud cover. "
                "No cloud-free Landsat 8 or 9 scene found for this AOI/date range — try widening the "
                "date window (Landsat's 16-day revisit means a short window can easily miss every pass)."
            ),
        }

    lst = col.map(
        lambda img: img.select("ST_B10").multiply(0.00341802).add(149.0).subtract(273.15).rename("LST_C")
    ).mean().clip(region)

    stats = lst.reduceRegion(
        reducer=ee.Reducer.mean().combine(ee.Reducer.minMax(), sharedInputs=True).combine(ee.Reducer.stdDev(), sharedInputs=True),
        geometry=region, scale=30, maxPixels=1e9, bestEffort=True, tileScale=4,
    ).getInfo()

    valid_px = col.select("ST_B10").mosaic().mask().reduceRegion(
        reducer=ee.Reducer.mean(), geometry=region, scale=30, maxPixels=1e9, bestEffort=True, tileScale=4,
    ).getInfo()
    valid_pixel_fraction = round((valid_px.get("ST_B10", 0) or 0), 4)

    return {
        "lst_celsius": {
            "mean": round(stats.get("LST_C_mean", 0) or 0, 2),
            "min": round(stats.get("LST_C_min", 0) or 0, 2),
            "max": round(stats.get("LST_C_max", 0) or 0, 2),
            "std_dev": round(stats.get("LST_C_stdDev", 0) or 0, 2),
        },
        "scene_count": scene_count,
        "source": source,
        "valid_pixel_fraction": valid_pixel_fraction,
        "aoi_area_km2": round(_region_area_km2(region), 3),
        # Same blue-to-red thermal ramp already used for LST elsewhere in
        # this codebase (satellite_imagery.py) — kept identical on
        # purpose so the same temperature range reads consistently
        # whether someone sees it in Analyze's PDF report or here.
        "map_layer": _tile_layer(lst, {"min": 0, "max": 45, "palette": ["#1a4d7a", "#4a9ec9", "#e8e88a", "#d97a41", "#8b2020"]}),
        "download_url": _download_url(lst, region, scale=30),
        "method": (
            f"Land Surface Temperature from {source} Collection 2 Level-2 thermal band (ST_B10), "
            f"<20% scene cloud cover, mean of {scene_count} scene(s), 30m native resolution. "
            f"Conversion: DN \u00d7 0.00341802 + 149.0 = Kelvin (USGS Collection 2 Level-2 Science Product "
            f"Guide), converted to Celsius. This is SURFACE (skin/canopy-top) temperature, not 2m air "
            f"temperature — the two can differ substantially, especially over bare soil, pavement, or "
            f"under strong sun, and should not be directly compared to weather-station air-temperature "
            f"readings without accounting for that difference."
        ),
    }


def compute_surface_water_dynamics(aoi: Dict) -> Dict[str, Any]:
    """Surface water extent and permanence from JRC Global Surface Water
    v1.4 (Pekel et al. 2016, Nature) — directly relevant to NRSC's
    reservoir/water-resources monitoring work: how much of this AOI is
    permanent water, how much is seasonal, and how reliably does water
    return to each spot year over year.

    No start/end dates: this is a fixed historical product covering
    1984-2021 (Landsat 5/7/8 derived, ~4.7M scenes), not a queryable
    live dataset — the "date range" is baked into the product itself,
    stated plainly in the method field below rather than left implicit.

    Deliberately does NOT use the dataset's 10-class `transition` band
    (permanent/new permanent/lost permanent/seasonal/etc.) — its class
    NAMES are well documented, but this implementation could not verify
    the exact numeric code each name maps to from an authoritative
    source, and shipping a mislabeled classification would misinform
    exactly the kind of careful reader this tool is for. Built instead
    from `occurrence` (0-100, unambiguous) and `max_extent` (binary,
    unambiguous), using a permanent/seasonal split at the 90% occurrence
    threshold JRC's own materials commonly cite — stated explicitly as
    this implementation's own choice, not an unstated assumption.
    """
    logger.info("GEE (remote sensing): surface_water_dynamics")
    region = _polygon_geometry(aoi)

    gsw = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").clip(region)
    occurrence = gsw.select("occurrence")
    max_extent = gsw.select("max_extent")
    seasonality = gsw.select("seasonality")

    permanent_mask = occurrence.gte(90)
    seasonal_mask = occurrence.gt(0).And(occurrence.lt(90))

    permanent_km2 = _calc_area_km2(permanent_mask, region, scale=30)
    seasonal_km2 = _calc_area_km2(seasonal_mask, region, scale=30)
    max_extent_km2 = _calc_area_km2(max_extent, region, scale=30)
    aoi_km2 = _region_area_km2(region)

    # Occurrence/seasonality stats computed only over pixels where water
    # was EVER observed (max_extent masks out permanently-dry land) —
    # averaging occurrence over the whole AOI including dry land would
    # just report near-zero everywhere for most polygons, which isn't
    # a useful "how does the water here behave" answer.
    water_only = occurrence.updateMask(max_extent)
    occ_stats = water_only.reduceRegion(
        reducer=ee.Reducer.mean().combine(ee.Reducer.stdDev(), sharedInputs=True),
        geometry=region, scale=30, maxPixels=1e9, bestEffort=True, tileScale=4,
    ).getInfo()
    seasonality_stats = seasonality.updateMask(max_extent).reduceRegion(
        reducer=ee.Reducer.mean(), geometry=region, scale=30, maxPixels=1e9, bestEffort=True, tileScale=4,
    ).getInfo()

    return {
        "permanent_water_km2": round(permanent_km2, 4),
        "seasonal_water_km2": round(seasonal_km2, 4),
        "max_ever_water_km2": round(max_extent_km2, 4),
        "aoi_area_km2": round(aoi_km2, 3),
        "occurrence_pct": {
            "mean": round(occ_stats.get("occurrence_mean", 0) or 0, 2),
            "std_dev": round(occ_stats.get("occurrence_stdDev", 0) or 0, 2),
        },
        "avg_months_per_year_water_present": round(seasonality_stats.get("seasonality", 0) or 0, 2),
        "permanent_threshold_used": "occurrence >= 90%",
        "map_layer": _tile_layer(occurrence, {
            "min": 0, "max": 100,
            # JRC's own official palette for the occurrence band —
            # matching it means anyone who's used this dataset in GEE's
            # own code editor or the JRC Explorer recognizes it instantly.
            "palette": ["#ffffff", "#ffbbbb", "#0000ff"],
        }),
        "download_url": _download_url(occurrence, region, scale=30),
        "method": (
            "JRC Global Surface Water v1.4 (Pekel, Cottam, Gorelick & Belward 2016, Nature; "
            "ee.Image('JRC/GSW1_4/GlobalSurfaceWater')), 30m, derived from 4.7M+ Landsat 5/7/8 scenes "
            "covering 1984-2021 — a fixed historical product, not imagery of the current date. "
            "Permanent water = occurrence >= 90% of valid observations (this tool's own threshold "
            "choice, not a value baked into the dataset); seasonal = occurrence > 0% and < 90%. "
            "avg_months_per_year_water_present is the mean of the seasonality band (0-12) over "
            "ever-wet pixels only. Free, no restriction of use, under the Copernicus Programme."
        ),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Supervised classification — Random Forest (ee.Classifier.smileRandomForest),
# trained on user-drawn training points rather than a fixed pretrained
# product like lulc's ESA WorldCover above. This is the "classify into
# whatever categories THIS analysis actually needs" tool (custom land-use
# schemes, crop types, anything not covered by WorldCover's 11 fixed
# classes) — the ML/DL addition chosen after reviewing IIRS's "AI/ML for
# Geodata Analytics" outreach course syllabus (RF/SVM classification via
# GEE was the one piece of that syllabus that fit this project's existing
# GEE-native architecture directly; DL object detection and RNN
# forecasting were considered and deliberately NOT built — see
# memory/discussion — because they need labeled training data or history
# this project doesn't have yet).
# ═════════════════════════════════════════════════════════════════════════════

# Raw Sentinel-2 bands + the 3 indices already defined in INDEX_DEFINITIONS
# that separate vegetation/water/built-up well — enough signal for a small
# user-drawn training set without so many correlated bands that overfitting
# on a handful of points gets worse instead of better.
FEATURE_BANDS = ["B2", "B3", "B4", "B8", "B11", "B12", "NDVI", "NDWI", "NDBI"]

# Distinct qualitative colors (trimmed from Sasha Trubetskoy's "20 distinct
# colors" palette), assigned to classes in the order they first appear in
# training_samples — these are arbitrary user-defined classes, not tied to
# an external classification scheme the way WORLDCOVER_PALETTE is.
CLASS_PALETTE = ["#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
                  "#911eb4", "#46f0f0", "#f032e6", "#bcf60c", "#fabebe"]

# A held-out test split only means something if there are enough points per
# class to hold any out at all; capped at len(CLASS_PALETTE) classes since
# a Random Forest trained on a handful of user-drawn points per class
# doesn't hold up meaningfully past this many categories anyway.
MIN_SAMPLES_PER_CLASS = 4
MIN_CLASSES = 2
MAX_CLASSES = len(CLASS_PALETTE)
DEFAULT_NUM_TREES = 50
_TEST_SPLIT_SEED = 42


def _validate_training_samples(training_samples: list):
    if not training_samples:
        raise ValueError("At least one training sample is required — add training points and assign each a class.")
    by_class: Dict[int, list] = {}
    for s in training_samples:
        by_class.setdefault(int(s["class_id"]), []).append(s)
    if len(by_class) < MIN_CLASSES:
        raise ValueError(f"At least {MIN_CLASSES} distinct classes are needed for classification (got {len(by_class)}).")
    if len(by_class) > MAX_CLASSES:
        raise ValueError(f"At most {MAX_CLASSES} classes are supported (got {len(by_class)}) — a Random Forest trained on a small user-drawn set this small doesn't hold up meaningfully past this many categories.")
    thin = {cid: len(pts) for cid, pts in by_class.items() if len(pts) < MIN_SAMPLES_PER_CLASS}
    if thin:
        raise ValueError(
            f"Each class needs at least {MIN_SAMPLES_PER_CLASS} training points (some are held out "
            f"for an honest accuracy estimate, not used for training). Add more points for class id(s): {list(thin)}."
        )


def _stratified_split(training_samples: list, test_frac: float = 0.3, seed: int = _TEST_SPLIT_SEED) -> list:
    """Splits WITHIN each class (not one global random split) so every
    class contributes to both the training set and the held-out test
    set — a single global random split could easily leave a whole class
    with zero test points by chance, especially at the small n this tool
    is designed for. Returns the same dicts with a 'split' key added."""
    import random
    rng = random.Random(seed)
    by_class: Dict[int, list] = {}
    for s in training_samples:
        by_class.setdefault(int(s["class_id"]), []).append(s)

    out = []
    for pts in by_class.values():
        pts = list(pts)
        rng.shuffle(pts)
        n_test = max(1, round(len(pts) * test_frac))
        n_test = min(n_test, len(pts) - 2)  # always leave >=2 points to train on
        for i, p in enumerate(pts):
            out.append({**p, "split": "test" if i < n_test else "train"})
    return out


def _feature_image(composite: ee.Image) -> ee.Image:
    raw = composite.select(["B2", "B3", "B4", "B8", "B11", "B12"])
    ndvi, ndwi, ndbi = _index_image(composite, "ndvi"), _index_image(composite, "ndwi"), _index_image(composite, "ndbi")
    return raw.addBands([ndvi, ndwi, ndbi])


def _train_rf_classifier(region: ee.Geometry, start_date: str, end_date: str, training_samples: list, num_trees: int):
    """Shared by compute_supervised_classification (full stats + map) and
    get_report_thumbnail (just the classified image) — trains once, both
    callers reuse the result rather than re-deriving the classifier
    independently. Returns a dict of everything either caller needs."""
    composite, img_count = _s2_composite(region, start_date, end_date)
    feature_img = _feature_image(composite)

    class_labels: Dict[int, str] = {}
    for s in training_samples:
        class_labels.setdefault(int(s["class_id"]), s.get("class_label") or f"Class {s['class_id']}")

    samples_with_split = _stratified_split(training_samples)
    training_fc = ee.FeatureCollection([
        ee.Feature(ee.Geometry.Point([s["lon"], s["lat"]]), {"class": int(s["class_id"]), "split": s["split"]})
        for s in samples_with_split
    ])

    sampled = feature_img.sampleRegions(collection=training_fc, properties=["class", "split"], scale=10, tileScale=4)
    # Points on a cloud-masked pixel come back with null band values —
    # drop them rather than let a null poison training; report how many
    # were dropped so a caller can see if a lot of points fell in cloud.
    sampled = sampled.filter(ee.Filter.notNull(FEATURE_BANDS))
    n_sampled = sampled.size().getInfo()

    train_fc = sampled.filter(ee.Filter.eq("split", "train"))
    test_fc = sampled.filter(ee.Filter.eq("split", "test"))
    n_train, n_test = train_fc.size().getInfo(), test_fc.size().getInfo()
    if n_train == 0:
        raise ValueError("No training points had valid (cloud-free) pixel data in this date range — widen the date range or check the points fall inside the AOI.")

    classifier = ee.Classifier.smileRandomForest(numberOfTrees=num_trees, seed=_TEST_SPLIT_SEED).train(
        features=train_fc, classProperty="class", inputProperties=FEATURE_BANDS,
    )
    classified_image = feature_img.classify(classifier)

    return {
        "classified_image": classified_image, "classifier": classifier, "test_fc": test_fc,
        "class_labels": class_labels, "n_provided": len(training_samples), "n_sampled": n_sampled,
        "n_train": n_train, "n_test": n_test, "img_count": img_count,
    }


def compute_supervised_classification(aoi: Dict, start_date: str, end_date: str, training_samples: list, num_trees: int = DEFAULT_NUM_TREES) -> Dict[str, Any]:
    """Random Forest supervised classification (ee.Classifier.smileRandomForest
    — Breiman 2001; GEE's implementation via the SMILE Java ML library) on a
    cloud-masked Sentinel-2 median composite, trained on user-supplied
    training points — for classifying into whatever categories THIS
    analysis needs (crop types, a custom land-use scheme), not a fixed
    global product like the lulc tool's ESA WorldCover.

    Accuracy is reported two ways and the two are NOT interchangeable:
    training_resubstitution_accuracy is the classifier scored against the
    same points it trained on — always optimistic, reported for
    transparency only. test_accuracy is computed on a held-out ~30% split
    (stratified per class — see _stratified_split) the classifier never
    saw — this is the honest number, though with the small training sets
    this tool is designed for it still carries real uncertainty, especially
    below ~10 test points per class.
    """
    logger.info("GEE (remote sensing): supervised_classification")
    _validate_training_samples(training_samples)
    region = _polygon_geometry(aoi)

    trained = _train_rf_classifier(region, start_date, end_date, training_samples, num_trees)
    classified_image, classifier = trained["classified_image"], trained["classifier"]
    class_labels, test_fc, n_test = trained["class_labels"], trained["test_fc"], trained["n_test"]

    train_accuracy = round(classifier.confusionMatrix().accuracy().getInfo(), 3)
    train_kappa = round(classifier.confusionMatrix().kappa().getInfo(), 3)

    test_accuracy = test_kappa = None
    if n_test > 0:
        test_classified = test_fc.classify(classifier)
        test_matrix = test_classified.errorMatrix("class", "classification")
        test_accuracy = round(test_matrix.accuracy().getInfo(), 3)
        test_kappa = round(test_matrix.kappa().getInfo(), 3)

    area_img = ee.Image.pixelArea().divide(1_000_000).addBands(classified_image)
    hist = area_img.reduceRegion(
        reducer=ee.Reducer.sum().group(groupField=1, groupName="class"),
        geometry=region, scale=10, maxPixels=1e10, bestEffort=True, tileScale=4,
    ).getInfo()
    groups = hist.get("groups", [])
    total_km2 = sum(g["sum"] for g in groups) or 1
    classes_out = []
    for g in groups:
        cid = int(g["class"])
        classes_out.append({
            "class_id": cid, "label": class_labels.get(cid, f"Class {cid}"),
            "area_km2": round(g["sum"], 3), "pct_of_aoi": round(g["sum"] / total_km2 * 100, 2),
        })
    classes_out.sort(key=lambda c: c["area_km2"], reverse=True)

    ordered_class_ids = sorted(class_labels.keys())
    dense = classified_image.remap(ordered_class_ids, list(range(len(ordered_class_ids))))
    palette = CLASS_PALETTE[:len(ordered_class_ids)]

    return {
        "classes": classes_out,
        "class_legend": [{"class_id": cid, "label": class_labels[cid], "color": palette[i]} for i, cid in enumerate(ordered_class_ids)],
        "total_classified_km2": round(total_km2, 3),
        "aoi_area_km2": round(_region_area_km2(region), 3),
        "training": {
            "points_provided": trained["n_provided"], "points_used": trained["n_sampled"],
            "points_dropped_cloud_masked": trained["n_provided"] - trained["n_sampled"],
            "train_points": trained["n_train"], "test_points": n_test, "num_trees": num_trees,
        },
        "accuracy": {
            "training_resubstitution_accuracy": train_accuracy,
            "training_resubstitution_kappa": train_kappa,
            "test_accuracy": test_accuracy,
            "test_kappa": test_kappa,
            "note": (
                "training_resubstitution_accuracy is scored on the same points the model trained on "
                "and is always optimistic — test_accuracy, on a held-out ~30% split the model never "
                "saw, is the honest estimate. test_accuracy is null if too few points survived "
                "cloud-masking to hold any out."
            ),
        },
        "map_layer": _tile_layer(dense, {"min": 0, "max": max(len(ordered_class_ids) - 1, 1), "palette": palette}),
        "download_url": _download_url(classified_image, region, scale=10),
        "method": (
            f"Random Forest supervised classification (ee.Classifier.smileRandomForest, {num_trees} trees, "
            f"seed={_TEST_SPLIT_SEED}; Breiman 2001), trained on {trained['n_train']} user-provided training "
            f"points across {len(ordered_class_ids)} classes (held out {n_test} points for testing). "
            f"Feature bands: {', '.join(FEATURE_BANDS)} from a cloud-masked Sentinel-2 SR median composite "
            f"({trained['img_count']} scenes, {start_date} to {end_date}), 10m."
        ),
    }


def get_report_thumbnail(tool: str, aoi: Dict, **params) -> Optional[bytes]:
    """Static PNG thumbnail for a Spectra PDF report — reconstructs the
    minimal image for `tool` (same underlying data/visualization as its
    live map_layer, see the matching compute_* function above) and
    fetches a rendered PNG via satellite_imagery.py's _fetch_thumb_bytes.

    Deliberately separate from the compute_* functions rather than
    having them return a raw ee.Image: those functions cross an async
    job_store/HTTP boundary and only ever return JSON-serializable
    dicts, so there's no image object left by the time report
    generation happens in a later, separate request. This re-derives
    just the image, not the full stats — cheaper than a full recompute,
    and kept in this module (not satellite_imagery.py) since it reuses
    this module's AOI/tool-specific construction logic directly.

    Returns None for atmospheric_composition (no meaningful spatial
    picture at ~1.1km resolution over a typical AOI — same reasoning as
    why it has no map_layer) and on any GEE failure, rather than
    raising — a report should still generate without its imagery page
    if the thumbnail fetch fails, not fail outright.
    """
    try:
        region = _polygon_geometry(aoi)
        if tool == "spectral_indices":
            start_date, end_date = params["start_date"], params["end_date"]
            index_id = (params.get("indices") or ["ndvi"])[0]
            composite, _ = _s2_composite(region, start_date, end_date)
            img = _index_image(composite, index_id)
            return _fetch_thumb_bytes(img, region, INDEX_PALETTES.get(index_id, {"min": -1, "max": 1, "palette": ["#000000", "#ffffff"]}))
        if tool == "terrain":
            dem = ee.Image("COPERNICUS/DEM/GLO30").select("DEM").clip(region)
            return _fetch_thumb_bytes(ee.Terrain.hillshade(dem), region, {"min": 0, "max": 255})
        if tool == "lulc":
            wc = ee.ImageCollection("ESA/WorldCover/v200").first().select("Map").clip(region)
            class_codes = sorted(WORLDCOVER_CLASSES.keys())
            wc_dense = wc.remap(class_codes, list(range(len(class_codes))))
            return _fetch_thumb_bytes(wc_dense, region, {"min": 0, "max": len(class_codes) - 1, "palette": WORLDCOVER_PALETTE})
        if tool == "snow_cover":
            start_date, end_date = params["start_date"], params["end_date"]
            composite, _ = _s2_composite(region, start_date, end_date)
            ndsi = composite.normalizedDifference(["B3", "B11"]).rename("NDSI")
            return _fetch_thumb_bytes(ndsi.gt(NDSI_SNOW_THRESHOLD).selfMask(), region, {"palette": ["#4292c6"]})
        if tool == "sar_backscatter":
            start_date, end_date = params["start_date"], params["end_date"]
            col = (ee.ImageCollection("COPERNICUS/S1_GRD")
                   .filterBounds(region).filterDate(ee.Date(start_date), ee.Date(end_date))
                   .filter(ee.Filter.eq("instrumentMode", "IW"))
                   .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
                   .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH")))
            composite = col.select(["VV", "VH"]).median().clip(region)
            vv_lin, vh_lin = ee.Image(10).pow(composite.select("VV").divide(10)), ee.Image(10).pow(composite.select("VH").divide(10))
            rvi = vh_lin.multiply(4).divide(vv_lin.add(vh_lin)).rename("RVI")
            return _fetch_thumb_bytes(rvi, region, {"min": 0, "max": 1, "palette": ["#f7fcf5", "#74c476", "#00441b"]})
        if tool == "change_detection":
            index_id = params["index"]
            c1, _ = _s2_composite(region, params["period1_start"], params["period1_end"])
            c2, _ = _s2_composite(region, params["period2_start"], params["period2_end"])
            delta_img = _index_image(c2, index_id).subtract(_index_image(c1, index_id)).rename("delta")
            return _fetch_thumb_bytes(delta_img, region, {"min": -0.3, "max": 0.3, "palette": ["#a50026", "#ffffbf", "#1a9850"]})
        if tool == "burn_severity":
            c_pre, _ = _s2_composite(region, params["pre_start"], params["pre_end"])
            c_post, _ = _s2_composite(region, params["post_start"], params["post_end"])
            nbr_pre = c_pre.normalizedDifference(["B8", "B12"]).rename("NBR")
            nbr_post = c_post.normalizedDifference(["B8", "B12"]).rename("NBR")
            dnbr = nbr_pre.subtract(nbr_post).rename("dNBR")
            return _fetch_thumb_bytes(dnbr, region, {"min": -0.25, "max": 0.66, "palette": ["#1a9850", "#66bd63", "#ffffbf", "#fdae61", "#f46d43", "#a50026"]})
        if tool == "land_surface_temperature":
            start_date, end_date = params["start_date"], params["end_date"]
            end_ee = _cap_end_date(end_date)

            def _lst_collection(coll_id):
                return (ee.ImageCollection(coll_id).filterBounds(region)
                        .filterDate(ee.Date(start_date), end_ee).filter(ee.Filter.lt("CLOUD_COVER", 20)))
            col = _lst_collection("LANDSAT/LC08/C02/T1_L2")
            if col.size().getInfo() == 0:
                col = _lst_collection("LANDSAT/LC09/C02/T1_L2")
            if col.size().getInfo() == 0:
                return None
            lst = col.map(lambda img: img.select("ST_B10").multiply(0.00341802).add(149.0).subtract(273.15).rename("LST_C")).mean().clip(region)
            return _fetch_thumb_bytes(lst, region, {"min": 0, "max": 45, "palette": ["#1a4d7a", "#4a9ec9", "#e8e88a", "#d97a41", "#8b2020"]})
        if tool == "surface_water_dynamics":
            occurrence = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence").clip(region)
            return _fetch_thumb_bytes(occurrence, region, {"min": 0, "max": 100, "palette": ["#ffffff", "#ffbbbb", "#0000ff"]})
        if tool == "supervised_classification":
            training_samples = params["training_samples"]
            trained = _train_rf_classifier(region, params["start_date"], params["end_date"], training_samples, params.get("num_trees") or DEFAULT_NUM_TREES)
            ordered_class_ids = sorted(trained["class_labels"].keys())
            dense = trained["classified_image"].remap(ordered_class_ids, list(range(len(ordered_class_ids))))
            palette = CLASS_PALETTE[:len(ordered_class_ids)]
            return _fetch_thumb_bytes(dense, region, {"min": 0, "max": max(len(ordered_class_ids) - 1, 1), "palette": palette})
        return None  # atmospheric_composition, index_time_series (a chart, not a raster)
    except Exception as e:
        logger.warning(f"get_report_thumbnail failed for tool={tool}: {type(e).__name__}: {e}")
        return None
