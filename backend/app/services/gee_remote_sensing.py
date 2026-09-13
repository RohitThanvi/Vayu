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
        }

    return {
        "indices": results,
        "method": f"Sentinel-2 SR Harmonized, cloud-masked via Scene Classification Layer, median composite of {scene_count} scene(s), 10-20m native resolution.",
        "scene_count": scene_count,
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

    return {
        "classes": classes,
        "total_classified_km2": round(total_km2, 3),
        "aoi_area_km2": round(_region_area_km2(region), 3),
        "method": "ESA WorldCover v200, 10m resolution, calendar year 2021, Sentinel-1+Sentinel-2-derived (Zanaga et al. 2022, doi:10.5281/zenodo.7254221). Overall validated accuracy 76.7%.",
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
        reducer=ee.Reducer.mean().combine(ee.Reducer.minMax(), sharedInputs=True),
        geometry=region, scale=20, maxPixels=1e9, bestEffort=True, tileScale=4,
    ).getInfo()

    return {
        "snow_covered_km2": round(snow_km2, 3),
        "aoi_area_km2": round(aoi_km2, 3),
        "snow_cover_pct": round((snow_km2 / aoi_km2 * 100) if aoi_km2 > 0 else 0, 2),
        "ndsi_mean": round(ndsi_stats.get("NDSI_mean", 0) or 0, 4),
        "ndsi_max": round(ndsi_stats.get("NDSI_max", 0) or 0, 4),
        "threshold_used": NDSI_SNOW_THRESHOLD,
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

    return {
        "vv_db": {"mean": round(stats.get("VV_mean", 0) or 0, 2), "std_dev": round(stats.get("VV_stdDev", 0) or 0, 2)},
        "vh_db": {"mean": round(stats.get("VH_mean", 0) or 0, 2), "std_dev": round(stats.get("VH_stdDev", 0) or 0, 2)},
        "radar_vegetation_index": round(rvi_stats.get("RVI", 0) or 0, 4),
        "scene_count": scene_count,
        "method": (
            f"Sentinel-1 GRD, IW mode, dual-pol (VV+VH), median composite of {scene_count} scene(s), 20m analysis "
            f"resolution. Backscatter reported in dB as provided by the GEE S1_GRD product. RVI = 4*VH/(VV+VH), "
            f"computed on linear (not dB) backscatter power — a rough proxy for vegetation structure/roughness, "
            f"higher over dense canopy, lower over smooth/bare surfaces and open water."
        ),
        "aoi_area_km2": round(_region_area_km2(region), 3),
    }
