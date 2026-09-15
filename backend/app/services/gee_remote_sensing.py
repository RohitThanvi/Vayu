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
from typing import Dict, Any

import ee

from .gee_client import (
    _polygon_geometry, _validate_date_range, _require_start_after,
    _cap_end_date, _mask_s2_clouds, _region_area_km2, _calc_area_km2,
)

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

    # Standard, roughly perceptually-even palettes for the indices most
    # likely to actually get looked at as a map rather than just a
    # number — not exhaustive, but covers vegetation/water/built-up.
    INDEX_PALETTES = {
        "ndvi": {"min": -0.2, "max": 0.8, "palette": ["#a50026", "#f46d43", "#fee08b", "#66bd63", "#1a9850"]},
        "savi": {"min": -0.2, "max": 0.8, "palette": ["#a50026", "#f46d43", "#fee08b", "#66bd63", "#1a9850"]},
        "evi": {"min": -0.2, "max": 0.8, "palette": ["#a50026", "#f46d43", "#fee08b", "#66bd63", "#1a9850"]},
        "ndwi": {"min": -0.5, "max": 0.5, "palette": ["#8c510a", "#f6e8c3", "#c7eae5", "#01665e"]},
        "mndwi": {"min": -0.5, "max": 0.5, "palette": ["#8c510a", "#f6e8c3", "#c7eae5", "#01665e"]},
        "ndbi": {"min": -0.3, "max": 0.3, "palette": ["#ffffcc", "#fed976", "#fd8d3c", "#e31a1c"]},
        "ndsi": {"min": -0.2, "max": 0.8, "palette": ["#08306b", "#4292c6", "#c6dbef", "#ffffff"]},
    }

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
        "method": "Copernicus DEM GLO-30 (30m, TanDEM-X-derived Digital Surface Model incl. buildings/vegetation, not bare-earth). Slope/aspect via ee.Terrain. Aspect averaged circularly (unit-vector mean), not arithmetically.",
        "aoi_area_km2": round(_region_area_km2(region), 3),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Land cover classification — ESA WorldCover v200 (10m, 2021, Sentinel-1+2-derived)
# ═════════════════════════════════════════════════════════════════════════════

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
    mean1 = stats1.get(band_name, 0) or 0
    mean2 = stats2.get(band_name, 0) or 0
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
