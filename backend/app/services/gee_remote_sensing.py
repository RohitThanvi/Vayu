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
    _cap_end_date, _mask_s2_clouds, _region_area_km2, _calc_area_km2, copernicus_dem,
)
from .satellite_imagery import _fetch_thumb_bytes
from . import trend_stats
from .trend_stats import mann_kendall_test
from .accuracy_stats import compute_confusion_matrix

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


def _scene_provenance(region: ee.Geometry, start: str, end: str,
                       collection_id: str = "COPERNICUS/S2_SR_HARMONIZED",
                       cloud_prop: str = "CLOUDY_PIXEL_PERCENTAGE") -> list:
    """The actual scene IDs/dates/cloud% that fed a composite for this
    region+date range — reproducibility metadata a remote sensing
    scientist expects (exactly which acquisitions went into this
    composite), not just an aggregate scene count. A second, independent
    query against the same collection/region/date filter _s2_composite
    already used, rather than changing _s2_composite's own return value
    (several existing call sites already depend on its current two-value
    return) — purely additive, doesn't touch any working composite logic."""
    try:
        col = ee.ImageCollection(collection_id).filterBounds(region).filterDate(ee.Date(start), _cap_end_date(end))

        def _meta(img):
            return ee.Feature(None, {
                "id": img.get("system:index"),
                "date": img.date().format("YYYY-MM-dd"),
                "cloud_pct": img.get(cloud_prop),
            })

        feats = ee.FeatureCollection(col.map(_meta)).sort("date").getInfo().get("features", [])
        return [f["properties"] for f in feats]
    except Exception as e:
        logger.warning(f"_scene_provenance failed for {start}-{end}: {type(e).__name__}: {e}")
        return []


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
        "scene_provenance": _scene_provenance(region, start_date, end_date),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Terrain analysis — Copernicus DEM GLO-30 (30m global DSM, TanDEM-X derived)
# ═════════════════════════════════════════════════════════════════════════════

def compute_terrain_analysis(aoi: Dict) -> Dict[str, Any]:
    """Elevation/slope/aspect statistics from the Copernicus DEM GLO-30
    (30m global Digital Surface Model, TanDEM-X-derived — see
    COPERNICUS/DEM/GLO30_2024_1 in the GEE catalog). No date range: this is a
    single static global DEM, not a time series."""
    logger.info("GEE (remote sensing): terrain_analysis")
    region = _polygon_geometry(aoi)

    dem = copernicus_dem()            # projection restored: Terrain ops on a bare mosaic gave slopes ~25x too small
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
        "vertical_datum": "EGM2008 (EPSG:3855) — a 0m reading here does NOT mean mean sea level; see COPERNICUS/DEM/GLO30_2024_1 documentation.",
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
        "scene_provenance": _scene_provenance(region, start_date, end_date),
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
        "period1": {"start": period1_start, "end": period1_end, "mean": round(mean1, 4), "std_dev": round(stats1.get(f"{band_name}_stdDev", 0) or 0, 4), "scene_count": count1, "scene_provenance": _scene_provenance(region, period1_start, period1_end)},
        "period2": {"start": period2_start, "end": period2_end, "mean": round(mean2, 4), "std_dev": round(stats2.get(f"{band_name}_stdDev", 0) or 0, 4), "scene_count": count2, "scene_provenance": _scene_provenance(region, period2_start, period2_end)},
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
    consistent with USGS convention).

    Also reports RBR (Relativized Burn Ratio, Parks et al. 2014):
    RBR = dNBR / (NBR_pre + 1.001). RBR was proposed specifically because
    dNBR's severity reading is biased by how much vegetation was present
    BEFORE the fire — the same dNBR value means something different in a
    dense forest than in sparse scrub. Parks et al. found RBR agrees
    better with field-measured burn severity (composite burn index) than
    dNBR across 18 fires. Reported alongside dNBR, not as a replacement
    for it, since RBR doesn't have a single universal severity-class
    threshold table the way dNBR's USGS FIREMON breakpoints do (Parks et
    al. calibrate RBR thresholds per study) — the dNBR classes below are
    applied to RBR too ONLY for direct side-by-side comparison, not
    because they're RBR's own literature-validated cutoffs."""
    logger.info(f"GEE (remote sensing): burn_severity pre={pre_start}->{pre_end} post={post_start}->{post_end}")
    region = _polygon_geometry(aoi)
    pre_composite, pre_count = _s2_composite(region, pre_start, pre_end)
    post_composite, post_count = _s2_composite(region, post_start, post_end)

    def _nbr(img):
        return img.normalizedDifference(["B8", "B12"]).rename("NBR")

    nbr_pre = _nbr(pre_composite)
    nbr_post = _nbr(post_composite)
    dnbr = nbr_pre.subtract(nbr_post).rename("dNBR")
    # RBR = dNBR / (NBR_pre + 1.001) — the +1.001 offset (not 1.0) is
    # Parks et al.'s own choice, specifically to guarantee the denominator
    # never reaches exactly zero (they tested smaller offsets and found
    # they degraded agreement with field data), not a rounding artifact.
    rbr = dnbr.divide(nbr_pre.add(1.001)).rename("RBR")

    dnbr_stats = dnbr.reduceRegion(
        reducer=ee.Reducer.mean().combine(ee.Reducer.minMax(), sharedInputs=True).combine(ee.Reducer.stdDev(), sharedInputs=True),
        geometry=region, scale=20, maxPixels=1e9, bestEffort=True, tileScale=4,
    ).getInfo()
    rbr_stats = rbr.reduceRegion(
        reducer=ee.Reducer.mean().combine(ee.Reducer.minMax(), sharedInputs=True).combine(ee.Reducer.stdDev(), sharedInputs=True),
        geometry=region, scale=20, maxPixels=1e9, bestEffort=True, tileScale=4,
    ).getInfo()

    # Area breakdown by severity class
    class_areas = {}
    for lo, hi, label in DNBR_SEVERITY_CLASSES:
        mask = dnbr.gte(lo).And(dnbr.lt(hi))
        km2 = _calc_area_km2(mask, region, scale=20)
        if km2 > 0:
            class_areas[label] = round(km2, 3)

    dnbr_mean = dnbr_stats.get("dNBR_mean", 0) or 0
    rbr_mean = rbr_stats.get("RBR_mean", 0) or 0
    dnbr_class = _classify_dnbr(dnbr_mean)
    rbr_class = _classify_dnbr(rbr_mean)  # dNBR's own thresholds applied to RBR for direct comparison only — see docstring

    return {
        "dnbr_mean": round(dnbr_mean, 4),
        "dnbr_min": round(dnbr_stats.get("dNBR_min", 0) or 0, 4),
        "dnbr_max": round(dnbr_stats.get("dNBR_max", 0) or 0, 4),
        "dnbr_std_dev": round(dnbr_stats.get("dNBR_stdDev", 0) or 0, 4),
        "overall_classification": dnbr_class,
        "rbr_mean": round(rbr_mean, 4),
        "rbr_min": round(rbr_stats.get("RBR_min", 0) or 0, 4),
        "rbr_max": round(rbr_stats.get("RBR_max", 0) or 0, 4),
        "rbr_std_dev": round(rbr_stats.get("RBR_stdDev", 0) or 0, 4),
        "rbr_classification": rbr_class,
        "dnbr_rbr_agree": dnbr_class == rbr_class,
        "area_by_severity_km2": class_areas,
        "aoi_area_km2": round(_region_area_km2(region), 3),
        "pre_fire_scenes": pre_count, "post_fire_scenes": post_count,
        "pre_fire_scene_provenance": _scene_provenance(region, pre_start, pre_end),
        "post_fire_scene_provenance": _scene_provenance(region, post_start, post_end),
        # USGS FIREMON's own conventional severity color ramp (blue-green
        # regrowth through yellow/orange/red severity) — same breakpoints
        # as DNBR_SEVERITY_CLASSES above, so the map and the tabulated
        # area-by-severity numbers describe the same classification.
        "map_layer": _tile_layer(dnbr, {
            "min": -0.25, "max": 0.66,
            "palette": ["#1a9850", "#66bd63", "#ffffbf", "#fdae61", "#f46d43", "#a50026"],
        }),
        "rbr_map_layer": _tile_layer(rbr, {
            "min": -0.25, "max": 0.66,
            "palette": ["#1a9850", "#66bd63", "#ffffbf", "#fdae61", "#f46d43", "#a50026"],
        }),
        "download_url": _download_url(dnbr, region, scale=20),
        "rbr_download_url": _download_url(rbr, region, scale=20),
        "method": (
            "dNBR (Key & Benson 2006, USGS FIREMON standard) = NBR_pre - NBR_post, where NBR = (B8-B12)/(B8+B12) "
            "on cloud-masked Sentinel-2 SR composites, 20m. Severity classes are the standard USGS breakpoints, "
            "not a custom threshold. RBR (Parks, Dillon & Miller 2014, doi:10.3390/rs6031827) = dNBR / (NBR_pre "
            "+ 1.001), corrects for pre-fire vegetation density bias in dNBR — reported alongside it, classified "
            "with the SAME dNBR breakpoints purely for side-by-side comparison (RBR's own literature thresholds "
            "are study-calibrated, not universal). Requires the pre-fire and post-fire periods to be specified "
            "correctly by the caller — this function has no way to verify a fire actually occurred in the given window."
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

    # Statistical trend significance (Mann-Kendall + Sen's slope) on the
    # non-gap points — a chart alone can't say whether an apparent trend
    # is real or just noise; this can. Silently skipped (not an error) if
    # too few non-gap points survive cloud gaps to run the test on.
    valid = [(p["date"], p["value"]) for p in points if p["value"] is not None]
    trend_analysis = mann_kendall_test([v for _, v in valid], [d_ for d_, _ in valid]) if len(valid) >= trend_stats.MIN_POINTS \
        else {"status": "insufficient_data", "note": f"Only {len(valid)} non-gap points — Mann-Kendall needs at least {trend_stats.MIN_POINTS}."}

    return {
        "index": index_id, "label": d["label"], "interval": interval,
        "points": points,
        "trend_analysis": trend_analysis,
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


def compute_soil_moisture(aoi: Dict, start_date: str, end_date: str) -> Dict[str, Any]:
    """Surface soil moisture from SMAP L4 (NASA/SMAP/SPL4SMGP/008), band
    "sm_surface" — the long-flagged open Spectra gap: Agri already has
    SMAP-based moisture via irrigation_advisory.py's dry-stress check, but
    it was never exposed as a standalone Spectra tool with its own
    map_layer/download_url/uncertainty like every other Spectra product.

    Same dataset (SPL4SMGP.008, replacing the deprecated
    NASA_USDA/HSL/SMAP10KM_soil_moisture product) and start-date floor as
    gee_client.py's compute_soil_moisture (Analyze tab), but that one does
    a before/after 3-month-window comparison for change detection — this
    is a single-period snapshot (mean over start_date..end_date, no fixed
    3-month padding) matching how every other Spectra tool here reports
    one period's stats, not a delta.
    """
    logger.info(f"GEE (remote sensing): soil_moisture {start_date} -> {end_date}")
    _validate_date_range(start_date, end_date)
    _require_start_after(start_date, "2015-04-01", "SMAP")
    region = _polygon_geometry(aoi)
    end_ee = _cap_end_date(end_date)

    col = (
        ee.ImageCollection("NASA/SMAP/SPL4SMGP/008")
        .filterBounds(region).filterDate(ee.Date(start_date), end_ee)
        .select("sm_surface")
    )
    scene_count = col.size().getInfo()
    if scene_count == 0:
        return {
            "sm_surface": None, "scene_count": 0,
            "method": (
                "NASA SMAP L4 (SPL4SMGP.008) surface soil moisture, band sm_surface, ~9km/3-hourly. "
                "No SMAP coverage found for this AOI/date range — try widening the date window."
            ),
        }

    sm = col.mean().clip(region)

    stats = sm.reduceRegion(
        reducer=ee.Reducer.mean().combine(ee.Reducer.minMax(), sharedInputs=True).combine(ee.Reducer.stdDev(), sharedInputs=True),
        geometry=region, scale=10000, maxPixels=1e9, bestEffort=True, tileScale=4,
    ).getInfo()

    valid_px = col.mosaic().mask().reduceRegion(
        reducer=ee.Reducer.mean(), geometry=region, scale=10000, maxPixels=1e9, bestEffort=True, tileScale=4,
    ).getInfo()
    valid_pixel_fraction = round((valid_px.get("sm_surface", 0) or 0), 4)

    return {
        "sm_surface": {
            "mean": round(stats.get("sm_surface_mean", 0) or 0, 4),
            "min": round(stats.get("sm_surface_min", 0) or 0, 4),
            "max": round(stats.get("sm_surface_max", 0) or 0, 4),
            "std_dev": round(stats.get("sm_surface_stdDev", 0) or 0, 4),
            "units": "m\u00b3/m\u00b3 (volumetric water content)",
        },
        "scene_count": scene_count,
        "valid_pixel_fraction": valid_pixel_fraction,
        "aoi_area_km2": round(_region_area_km2(region), 3),
        # Brown (dry) to blue (moist) — same sense as satellite_imagery.py's
        # existing SMAP thumbnail palette (get_soil_moisture_thumbnail),
        # kept consistent rather than inventing a second soil-moisture
        # color scheme in the same product. 0-0.5 m3/m3 covers the
        # physically realistic range for this band.
        "map_layer": _tile_layer(sm, {"min": 0, "max": 0.5, "palette": ["#a0522d", "#c9a86a", "#e8e2c8", "#8ab4cc", "#1a4d7a"]}),
        "download_url": _download_url(sm, region, scale=10000),
        "method": (
            f"Surface soil moisture (0\u20135cm depth) from NASA SMAP L4 (SPL4SMGP.008), band sm_surface, "
            f"mean of {scene_count} scene(s) between {start_date} and {end_date}, ~9km native resolution "
            f"(reduced at 10km scale to match the grid). SMAP's coarse resolution is appropriate for "
            f"regional/district-scale monitoring, not field-level irrigation decisions."
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


# ═════════════════════════════════════════════════════════════════════════════
# Dynamic World — Google/WRI's pretrained, global, near-real-time land-cover
# model (Brown et al. 2022). The complement to compute_supervised_classification
# above: that tool is for classes only the analyst defines (nothing pretrained
# could exist for those); this one is for the common, fixed set of categories
# most people actually want most of the time — instant, zero training points,
# same result for every user since it's one shared model, not one trained per
# request.
# ═════════════════════════════════════════════════════════════════════════════

DYNAMIC_WORLD_CLASSES = {
    0: "Water", 1: "Trees", 2: "Grass", 3: "Flooded vegetation", 4: "Crops",
    5: "Shrub & scrub", 6: "Built area", 7: "Bare ground", 8: "Snow & ice",
}

# Dynamic World's own official label-band colors (dataset catalog page) —
# using these, not an invented palette, so the map matches what Google's
# own Dynamic World app and documentation show. Already a dense 0-8
# sequence matching the class codes directly, unlike WorldCover's
# 10/20/.../100 codes — no remap needed before visualizing.
DYNAMIC_WORLD_PALETTE = [
    "419bdf", "397d49", "88b053", "7a87c6", "e49635", "dfc35a", "c4281b", "a59b8f", "b39fe1",
]


def compute_dynamic_world_classification(aoi: Dict, start_date: str, end_date: str) -> Dict[str, Any]:
    """Google/WRI Dynamic World (GOOGLE/DYNAMICWORLD/V1) — a pretrained deep
    learning model run by Google on every cloud-free Sentinel-2 scene
    globally, near-real-time (Brown, C.F., Brumby, S.P., Guzder-Williams, B.
    et al. 2022, Sci Data 9, 251, doi:10.1038/s41597-022-01307-4). 10m, 9
    classes. Unlike compute_supervised_classification, nothing is trained
    per-request here — every caller gets the same underlying model, just
    composited over whatever date range they pick.

    Multiple Dynamic World images typically fall in a date range (a new one
    every 2-5 days per location) — takes the per-pixel MODE (most frequent
    class) of the "label" band across all of them in range, the aggregation
    method Google's own Dynamic World tutorial recommends for a
    multi-temporal composite, rather than picking a single scene."""
    logger.info("GEE (remote sensing): dynamic_world_classification")
    region = _polygon_geometry(aoi)

    col = ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1").filterBounds(region).filterDate(start_date, end_date)
    n = col.size().getInfo()
    if n == 0:
        raise ValueError(f"No Dynamic World coverage for this AOI between {start_date} and {end_date} — try widening the date range.")

    label_mode = col.select("label").mode().clip(region)
    area_img = ee.Image.pixelArea().divide(1_000_000).addBands(label_mode)
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
            "code": code, "label": DYNAMIC_WORLD_CLASSES.get(code, f"Unknown class {code}"),
            "area_km2": round(g["sum"], 3), "pct_of_aoi": round(g["sum"] / total_km2 * 100, 2),
        })
    classes.sort(key=lambda c: c["area_km2"], reverse=True)

    return {
        "classes": classes,
        "total_classified_km2": round(total_km2, 3),
        "aoi_area_km2": round(_region_area_km2(region), 3),
        "scenes_used": n,
        "map_layer": _tile_layer(label_mode, {"min": 0, "max": 8, "palette": DYNAMIC_WORLD_PALETTE}),
        "download_url": _download_url(label_mode, region, scale=10),
        "method": (
            f"Google/WRI Dynamic World V1 (GOOGLE/DYNAMICWORLD/V1), 10m, near-real-time deep-learning "
            f"land cover, 9 fixed classes (Brown et al. 2022, doi:10.1038/s41597-022-01307-4). Per-pixel "
            f"mode of the label band across {n} scene(s) between {start_date} and {end_date}. Pretrained "
            f"and shared — the same model classifies every request, unlike the custom Random Forest tool, "
            f"which trains fresh per-request on your own points. CC-BY 4.0: produced for the Dynamic World "
            f"Project by Google in partnership with National Geographic Society and the World Resources Institute."
        ),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Flood mapping — Sentinel-1 SAR before/after change detection, following
# UN-SPIDER's own Recommended Practice for GEE flood mapping (un-spider.org/
# advisory-support/recommended-practices/recommended-practice-google-earth-
# engine-flood-mapping), verified against their published methodology
# rather than invented. All-weather, day/night SAR — can map a flood
# through the exact cloud cover that usually accompanies one, unlike every
# optical tool in this module.
# ═════════════════════════════════════════════════════════════════════════════

# UN-SPIDER's own documented threshold on the after/before ratio, computed
# directly on the dB-scale GRD bands as their published script does — NOT
# converted to linear power first (a ratio of dB values isn't physically a
# power ratio, but 1.25 is specifically calibrated against THEIR literal
# dB-domain divide; "fixing" the math to be linear and reapplying 1.25
# would silently change what the number means, the same reasoning RBR's
# "+1.001" offset gets in compute_burn_severity — replicate the cited
# method's actual arithmetic, not a purist version of it).
FLOOD_RATIO_THRESHOLD = 1.25
# ~50m circular smoothing to reduce speckle before the ratio — the radius
# UN-SPIDER's own script applies, not an arbitrary choice.
FLOOD_SPECKLE_SMOOTHING_RADIUS_M = 50


def compute_flood_mapping(aoi: Dict, pre_start: str, pre_end: str, post_start: str, post_end: str, polarization: str = "VH") -> Dict[str, Any]:
    """Sentinel-1 SAR flood extent via before/after change detection —
    UN-SPIDER's Recommended Practice. VH polarization by default (more
    sensitive to land-surface change generally); VV is offered as an
    alternative since it's more useful for delineating open water
    specifically (shoreline detection, a large post-flood water body),
    per UN-SPIDER's own guidance on the choice between the two.

    Both periods are filtered to the SAME orbit pass direction (ascending
    or descending — whichever has more pre-flood coverage, then the same
    direction is required for the post-flood period too) since mixing
    look geometries creates a false change signal from differing view
    angle, not an actual surface change. Permanent water (JRC Global
    Surface Water occurrence >= 90% — the same dataset and threshold
    compute_surface_water_dynamics uses) is masked out, since a lake
    being a lake isn't a flood.

    Requires the pre-flood and post-flood periods to be specified
    correctly by the caller — this function has no way to verify a flood
    actually occurred in the given window (same caveat compute_burn_severity
    states for fire windows). Scene-level provenance isn't reported here
    the way it is for the Sentinel-2 composite tools — Sentinel-1's extra
    mode/polarization/orbit filtering would need a purpose-built listing
    rather than the shared _scene_provenance helper (built for the S2_SR
    collection), left as a smaller follow-up rather than blocking this tool."""
    logger.info(f"GEE (remote sensing): flood_mapping pre={pre_start}->{pre_end} post={post_start}->{post_end} pol={polarization}")
    if polarization not in ("VH", "VV"):
        raise ValueError("polarization must be 'VH' or 'VV'.")
    region = _polygon_geometry(aoi)
    _require_start_after(pre_start, "2014-04-01", "Sentinel-1")

    def _s1_col(start, end):
        return (
            ee.ImageCollection("COPERNICUS/S1_GRD")
            .filterBounds(region)
            .filterDate(ee.Date(start), _cap_end_date(end))
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", polarization))
        )

    pre_all, post_all = _s1_col(pre_start, pre_end), _s1_col(post_start, post_end)

    pre_asc_n = pre_all.filter(ee.Filter.eq("orbitProperties_pass", "ASCENDING")).size().getInfo()
    pre_desc_n = pre_all.filter(ee.Filter.eq("orbitProperties_pass", "DESCENDING")).size().getInfo()
    if pre_asc_n == 0 and pre_desc_n == 0:
        raise ValueError(f"No Sentinel-1 IW {polarization} imagery found for the pre-flood period {pre_start} - {pre_end}. Try a wider date range.")
    orbit = "ASCENDING" if pre_asc_n >= pre_desc_n else "DESCENDING"

    pre_col = pre_all.filter(ee.Filter.eq("orbitProperties_pass", orbit))
    post_col = post_all.filter(ee.Filter.eq("orbitProperties_pass", orbit))
    pre_n, post_n = pre_col.size().getInfo(), post_col.size().getInfo()
    if post_n == 0:
        raise ValueError(
            f"No {orbit.lower()}-pass Sentinel-1 imagery found for the post-flood period {post_start} - "
            f"{post_end} (the pre-flood period used {orbit.lower()} pass, {pre_n} scene(s)) — widen the "
            f"post-flood date range."
        )

    pre_composite = pre_col.select(polarization).mean().focal_mean(FLOOD_SPECKLE_SMOOTHING_RADIUS_M, "circle", "meters")
    post_composite = post_col.select(polarization).mean().focal_mean(FLOOD_SPECKLE_SMOOTHING_RADIUS_M, "circle", "meters")

    ratio = post_composite.divide(pre_composite)
    flood_raw = ratio.gt(FLOOD_RATIO_THRESHOLD)

    permanent_water = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence").gte(90)
    flood_mask = flood_raw.And(permanent_water.Not()).rename("flood")

    flood_km2 = _calc_area_km2(flood_mask, region, scale=20)
    aoi_km2 = _region_area_km2(region)
    permanent_water_km2 = _calc_area_km2(permanent_water.clip(region), region, scale=30)

    return {
        "flood_extent_km2": round(flood_km2, 3),
        "aoi_area_km2": round(aoi_km2, 3),
        "pct_of_aoi_flooded": round(flood_km2 / aoi_km2 * 100, 2) if aoi_km2 > 0 else 0,
        "permanent_water_km2": round(permanent_water_km2, 3),
        "orbit_pass_used": orbit,
        "polarization": polarization,
        "pre_flood_scenes": pre_n, "post_flood_scenes": post_n,
        "map_layer": _tile_layer(flood_mask.selfMask(), {"palette": ["#2166ac"]}),
        "download_url": _download_url(flood_mask, region, scale=20),
        "method": (
            f"Sentinel-1 GRD (IW mode, {polarization} polarization, {orbit.lower()} pass only to avoid "
            f"mixed look-angle artifacts) before/after change detection — UN-SPIDER's Recommended Practice "
            f"for GEE flood mapping. {FLOOD_SPECKLE_SMOOTHING_RADIUS_M}m circular speckle smoothing, then "
            f"post/pre ratio (on dB-scale values, matching the published script's literal arithmetic) "
            f"thresholded at {FLOOD_RATIO_THRESHOLD}. Permanent water (JRC Global Surface Water occurrence "
            f">= 90%) masked out. All-weather, day/night SAR — usable under the cloud cover that typically "
            f"accompanies a flood event, unlike every optical tool in this module. {pre_n} pre-flood / "
            f"{post_n} post-flood scenes, both {orbit.lower()} pass."
        ),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Accuracy assessment — confusion matrix / kappa (Congalton 1991) against
# user-supplied reference points, for classification tools that otherwise
# report a result with no check against independent ground truth (lulc,
# dynamic_world, burn_severity's severity classes). The ML Classify tool
# already has this rigor via its own train/test split; this generalizes it
# to tools that use a FIXED classification scheme rather than training
# one, where "held-out training points" doesn't apply but "check against
# reference points the tool never saw" still does and still matters.
# ═════════════════════════════════════════════════════════════════════════════

ACCURACY_ASSESSABLE_TOOLS = {"lulc", "dynamic_world", "burn_severity"}


def _classified_image_for_accuracy(tool: str, region: ee.Geometry, params: Dict[str, Any]):
    """Returns (classified_image, class_code_to_label) for a tool that
    supports accuracy assessment — built from the SAME classified image/
    band each tool's own compute_* function produces, not a re-derived
    approximation, so this validates what the user actually sees."""
    if tool == "lulc":
        wc = ee.ImageCollection("ESA/WorldCover/v200").first().select("Map")
        return wc, WORLDCOVER_CLASSES
    if tool == "dynamic_world":
        start_date, end_date = params.get("start_date"), params.get("end_date")
        if not start_date or not end_date:
            raise ValueError("dynamic_world accuracy assessment requires start_date and end_date (the same window the classification itself used).")
        col = ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1").filterBounds(region).filterDate(start_date, end_date)
        if col.size().getInfo() == 0:
            raise ValueError(f"No Dynamic World coverage for {start_date} to {end_date} — try widening the date range.")
        return col.select("label").mode(), DYNAMIC_WORLD_CLASSES
    if tool == "burn_severity":
        pre_start, pre_end = params.get("pre_start"), params.get("pre_end")
        post_start, post_end = params.get("post_start"), params.get("post_end")
        if not all([pre_start, pre_end, post_start, post_end]):
            raise ValueError("burn_severity accuracy assessment requires pre_start/pre_end/post_start/post_end (the same windows the severity map itself used).")
        pre_composite, _ = _s2_composite(region, pre_start, pre_end)
        post_composite, _ = _s2_composite(region, post_start, post_end)
        nbr_pre = pre_composite.normalizedDifference(["B8", "B12"])
        nbr_post = post_composite.normalizedDifference(["B8", "B12"])
        dnbr = nbr_pre.subtract(nbr_post)
        # Bin the continuous dNBR into the SAME DNBR_SEVERITY_CLASSES used
        # everywhere else in this file (compute_burn_severity's own area
        # breakdown, its map palette) — class codes 0..len-1, in the same
        # order as the list, defaulting to the last (highest-severity) bin
        # before any .where() call narrows pixels into their actual bin.
        classified = ee.Image(len(DNBR_SEVERITY_CLASSES) - 1)
        for i, (lo, hi, _label) in enumerate(DNBR_SEVERITY_CLASSES):
            classified = classified.where(dnbr.gte(lo).And(dnbr.lt(hi)), i)
        class_map = {i: label for i, (_lo, _hi, label) in enumerate(DNBR_SEVERITY_CLASSES)}
        return classified, class_map
    raise ValueError(f"Accuracy assessment isn't available for tool '{tool}'. Available: {sorted(ACCURACY_ASSESSABLE_TOOLS)}.")


def compute_accuracy_assessment(tool: str, aoi: Dict, reference_points: list, tool_params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    reference_points: [{"lat": .., "lon": .., "true_class": "<label>"}, ...]
    — true_class must match one of the tool's own class labels (returned
    as class_options on the response either way, so a caller can show the
    user exactly which labels are valid rather than guessing at typos).

    Samples the SAME classified image/band the tool's own compute_*
    function produces at each reference point, pairs the predicted label
    with the point's stated true_class, and runs the pairs through
    accuracy_stats.compute_confusion_matrix — Congalton-standard overall/
    producer's/user's accuracy + kappa against independent reference data,
    not a resubstitution shortcut."""
    logger.info(f"GEE (remote sensing): accuracy_assessment tool={tool}")
    if tool not in ACCURACY_ASSESSABLE_TOOLS:
        raise ValueError(f"Accuracy assessment isn't available for tool '{tool}'. Available: {sorted(ACCURACY_ASSESSABLE_TOOLS)}.")
    if not reference_points:
        raise ValueError("At least a few reference points (lat, lon, true_class) are required.")

    region = _polygon_geometry(aoi)
    tool_params = tool_params or {}
    classified, class_map = _classified_image_for_accuracy(tool, region, tool_params)
    label_by_code = {int(code): label for code, label in class_map.items()}
    valid_labels = sorted(set(label_by_code.values()))

    fc = ee.FeatureCollection([
        ee.Feature(ee.Geometry.Point([p["lon"], p["lat"]]), {"true_class": p["true_class"], "point_index": i})
        for i, p in enumerate(reference_points)
    ])
    sampled = classified.rename("predicted_code").sampleRegions(
        collection=fc, properties=["true_class", "point_index"], scale=10, tileScale=4,
    ).getInfo()
    features = sampled.get("features", [])

    pairs, dropped_no_data, dropped_unrecognized = [], 0, 0
    for f in features:
        props = f["properties"]
        code = props.get("predicted_code")
        true_label = props.get("true_class")
        if code is None:
            dropped_no_data += 1
            continue
        if true_label not in valid_labels:
            dropped_unrecognized += 1
            continue
        pairs.append((label_by_code.get(int(code), f"Unknown class {int(code)}"), true_label))

    result = compute_confusion_matrix(pairs)
    result.update({
        "tool": tool,
        "class_options": valid_labels,
        "points_provided": len(reference_points),
        "points_used": len(pairs),
        "points_dropped_no_data": dropped_no_data,
        "points_dropped_unrecognized_class": dropped_unrecognized,
    })
    return result

# ═════════════════════════════════════════════════════════════════════════════
# Spectral composites ("X-ray" views) — the SAME Sentinel-2 / Sentinel-1
# imagery the other tools here use, displayed through different band
# combinations so it reveals what true colour hides (burn scars and
# haze-obscured ground in SWIR, vegetation health in near-infrared, soil/rock
# contrast in the SWIR geology composites, cloud-penetrating radar).
#
# No new science: every recipe is a published/standard band combination, and
# the only thing computed beyond the imagery itself is per-channel AOI
# statistics (mean, std dev, share of pixels clipped by the display stretch)
# so the picture comes with an honest account of how much of it is real
# signal vs. display saturation. Band recipes were checked against the
# Sentinel Hub custom-scripts composites page, the Sentinel education band
# list (as reproduced in the QGIS-in-Mineral-Exploration docs and
# GISGeography) and NASA Earth Observatory's fire-imagery captions; the
# places where those sources disagree are recorded in each recipe's "notes".
# ═════════════════════════════════════════════════════════════════════════════

# Display stretch for the optical composites, in surface reflectance (0-1).
# 0 - 0.3 is the stretch Google's own Earth Engine catalog sample uses for the
# Sentinel-2 true-colour view; the same fixed stretch is used for every
# optical recipe so composites are comparable across AOIs and dates (a
# per-AOI percentile stretch would not be). This is a display choice, not a
# measurement — NIR over dense vegetation can exceed 0.3, which is why the
# result reports the share of pixels the stretch clips, per channel.
COMPOSITE_DISPLAY_MIN = 0.0
COMPOSITE_DISPLAY_MAX = 0.3

# SAR composite display stretch (dB). -20..0 dB for VV and VH is the range
# Sentinel Hub's published Sentinel-1 dB RGB example uses. The third channel
# here is the VV-VH difference in dB (= the VV/VH ratio on a log scale); its
# 0..20 dB display range is an ESTIMATE of mine, NOT taken from a source —
# Sentinel Hub's example scales its ratio channel differently.
SAR_COMPOSITE_DB_MIN = -20.0
SAR_COMPOSITE_DB_MAX = 0.0
SAR_RATIO_DISPLAY_MIN = 0.0   # ESTIMATE — display stretch only
SAR_RATIO_DISPLAY_MAX = 20.0  # ESTIMATE — display stretch only

S2_BAND_NAMES = {
    "B2": "Blue", "B3": "Green", "B4": "Red", "B8": "NIR",
    "B8A": "Narrow NIR (red edge 4)", "B11": "SWIR 1", "B12": "SWIR 2",
}

SPECTRAL_COMPOSITES = {
    "natural_color": {
        "label": "Natural colour (B4-B3-B2)", "sensor": "S2", "bands": ["B4", "B3", "B2"],
        "reveals": "What the eye would see — the reference view to compare every other composite against.",
        "citation": "Standard true-colour composite (Sentinel Hub custom-scripts, Sentinel-2 simple RGB composites).",
        "notes": "",
    },
    "color_infrared": {
        "label": "Colour infrared — vegetation health (B8-B4-B3)", "sensor": "S2", "bands": ["B8", "B4", "B3"],
        "reveals": ("Vegetation vigour: intact vegetation reflects near-infrared strongly and appears red; bare ground "
                    "appears light; burned areas absorb all three bands and appear dark."),
        "citation": ("NASA Earth Observatory (R. Simmon), BAER feature — NIR/red/green composite description; band set "
                     "per Sentinel Hub custom-scripts 'Color Infrared'."),
        "notes": "NIR over dense vegetation often exceeds the fixed display stretch — check the per-channel clipped share.",
    },
    "swir": {
        "label": "Short-wave infrared — burn scars, haze (B12-B8A-B4)", "sensor": "S2", "bands": ["B12", "B8A", "B4"],
        "reveals": ("Burn scars and bare/built-up ground: newly burned land reflects strongly in SWIR, vegetation shows in "
                    "shades of green, bare soil and built-up areas in brown. SWIR light penetrates haze and many kinds of "
                    "smoke, so ground can be visible where true colour is washed out — but thick cloud still blocks it."),
        "citation": ("Sentinel education band list (B12, B8A, B4) as reproduced in the QGIS-in-Mineral-Exploration docs and "
                     "GISGeography; burn/smoke behaviour per NASA Earth Observatory fire-imagery captions (Landsat SWIR/NIR "
                     "composites) and the Sentinel Hub custom-scripts SWIR description."),
        "notes": "Sentinel Hub's own script for this composite uses B08 (not B8A) in the green channel; B8A follows the Sentinel education list.",
    },
    "atmospheric_penetration": {
        "label": "Atmospheric penetration (B12-B11-B8A)", "sensor": "S2", "bands": ["B12", "B11", "B8A"],
        "reveals": ("Built from the two SWIR bands plus narrow NIR to look through atmospheric haze; the result resembles "
                    "false-colour infrared while staying clear, and water, snow and ice are sharply delineated."),
        "citation": ("Sentinel education band list ('Atmospheric Penetration'); description after the BD-SAT dataset paper "
                     "(arXiv:2406.05912)."),
        "notes": "BD-SAT lists B8 rather than B8A for the third band; B8A follows the Sentinel education list.",
    },
    "agriculture": {
        "label": "Agriculture — crop health & moisture (B11-B8-B2)", "sensor": "S2", "bands": ["B11", "B8", "B2"],
        "reveals": ("Crop health and moisture: SWIR is sensitive to water in plants and soil, NIR to dense vegetation; "
                    "healthy dense vegetation appears dark green."),
        "citation": "Sentinel Hub custom-scripts 'Agriculture' composite (B11, B08, B02); also the Sentinel education band list.",
        "notes": "",
    },
    "geology": {
        "label": "Geology — rock & soil contrast (B12-B11-B2)", "sensor": "S2", "bands": ["B12", "B11", "B2"],
        "reveals": ("Rock and soil contrasts: different rock types reflect SWIR light differently, so lithological boundaries "
                    "and faults can stand out where true colour looks uniform."),
        "citation": "Sentinel Hub custom-scripts 'Geology' composite (B12, B11, B02); also GISGeography's Sentinel-2 band-combination guide.",
        "notes": "The QGIS-in-Mineral-Exploration docs list a second geology recipe (B12, B4, B2); B12-B11-B2 is the one most sources agree on.",
    },
    "sar_composite": {
        "label": "SAR composite — sees through cloud (VV, VH, VV−VH)", "sensor": "S1", "bands": ["VV", "VH", "VV_minus_VH"],
        "reveals": ("Radar works through cloud and at night. Open water and other smooth surfaces scatter the signal away "
                    "from the sensor and look dark; cross-polarised VH is raised by volume scattering such as dense canopy "
                    "(the physical basis of the RVI in the SAR Backscatter tool). The third channel is the VV−VH difference "
                    "in dB, i.e. the VV/VH ratio on a log scale."),
        "citation": ("Earth Engine Sentinel-1 algorithm guide (backscatter in dB, 10·log10 σ°); VV / VH / VV-VH-ratio RGB "
                     "convention after Sentinel Hub's Sentinel-1 dB RGB example."),
        "notes": ("Single orbit pass only (the dominant one in the window) so every scene shares one viewing geometry. "
                  "Third-channel display range is an ESTIMATE, not a published value."),
    },
}


def _s1_composite_image(region: ee.Geometry, start_date: str, end_date: str) -> Dict[str, Any]:
    """Sentinel-1 IW dual-pol median in dB (VV, VH) plus VV_minus_VH, built
    from a single orbit pass. Taking the median in dB is identical to taking
    it in linear power (a median is an order statistic, so it is unchanged by
    the monotonic dB transform). Restricting to the dominant pass follows the
    same-orbit rule the flood_mapping tool already applies (UN-SPIDER)."""
    _require_start_after(start_date, "2014-04-01", "Sentinel-1")
    end_ee = _cap_end_date(end_date)
    base = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(region).filterDate(ee.Date(start_date), end_ee)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
    )
    if base.size().getInfo() == 0:
        raise ValueError(f"No dual-pol (VV+VH) Sentinel-1 IW imagery found for {start_date} - {end_date}. Try a wider date range.")
    n_asc = base.filter(ee.Filter.eq("orbitProperties_pass", "ASCENDING")).size().getInfo()
    n_desc = base.filter(ee.Filter.eq("orbitProperties_pass", "DESCENDING")).size().getInfo()
    orbit = "ASCENDING" if n_asc >= n_desc else "DESCENDING"
    col = base.filter(ee.Filter.eq("orbitProperties_pass", orbit))
    median = col.select(["VV", "VH"]).median()
    image = median.addBands(median.select("VV").subtract(median.select("VH")).rename("VV_minus_VH"))
    return {"image": image, "collection": col, "orbit": orbit, "scene_count": max(n_asc, n_desc),
            "scenes_ascending": n_asc, "scenes_descending": n_desc}


def _s1_scene_list(col: "ee.ImageCollection") -> list:
    """Scene IDs/dates behind a Sentinel-1 composite (same idea as
    _scene_provenance, but S1 has no cloud percentage — it reports the orbit
    pass instead)."""
    try:
        def _meta(img):
            return ee.Feature(None, {
                "id": img.get("system:index"),
                "date": img.date().format("YYYY-MM-dd"),
                "orbit_pass": img.get("orbitProperties_pass"),
            })
        feats = ee.FeatureCollection(col.map(_meta)).sort("date").getInfo().get("features", [])
        return [f["properties"] for f in feats]
    except Exception as e:
        logger.warning(f"_s1_scene_list failed: {type(e).__name__}: {e}")
        return []


def _mean_std_reducer():
    return ee.Reducer.mean().combine(ee.Reducer.stdDev(), sharedInputs=True)


def compute_spectral_composites(aoi: Dict, start_date: str, end_date: str, composites: list = None) -> Dict[str, Any]:
    """Band-combination ("X-ray") views of the same imagery: natural colour,
    colour infrared, SWIR, atmospheric penetration, agriculture, geology
    (all Sentinel-2 SR Harmonized, cloud-masked median composite) and a
    Sentinel-1 SAR composite. Each composite returns its method/citation, a
    pixel-level map layer, a GeoTIFF of the underlying RAW bands (reflectance
    or dB — not the stretched picture), per-channel AOI mean/std dev, and the
    share of pixels clipped by the display stretch. Default: all of them.
    A composite that cannot be built (e.g. no Sentinel-1 scenes) is listed
    under 'unavailable' instead of failing the whole request."""
    logger.info(f"GEE (remote sensing): spectral_composites {composites} {start_date} -> {end_date}")
    composites = composites or list(SPECTRAL_COMPOSITES.keys())
    unknown = [c for c in composites if c not in SPECTRAL_COMPOSITES]
    if unknown:
        raise ValueError(f"Unknown composite(s): {unknown}. Valid: {list(SPECTRAL_COMPOSITES)}")
    _validate_date_range(start_date, end_date)
    region = _polygon_geometry(aoi)

    built: Dict[str, Any] = {}
    unavailable: Dict[str, str] = {}
    scene_count = None
    valid_pixel_fraction = None
    scene_provenance: list = []

    optical_ids = [c for c in composites if SPECTRAL_COMPOSITES[c]["sensor"] == "S2"]
    if optical_ids:
        try:
            composite, scene_count = _s2_composite(region, start_date, end_date)
        except ValueError as e:
            for c in optical_ids:
                unavailable[c] = str(e)
        else:
            valid_px = composite.select("B4").mask().reduceRegion(
                reducer=ee.Reducer.mean(), geometry=region, scale=20, maxPixels=1e9, bestEffort=True, tileScale=4,
            ).getInfo()
            valid_pixel_fraction = round((valid_px.get("B4", 0) or 0), 4)
            scene_provenance = _scene_provenance(region, start_date, end_date)

            union = sorted({b for c in optical_ids for b in SPECTRAL_COMPOSITES[c]["bands"]})
            stats = composite.select(union).reduceRegion(
                reducer=_mean_std_reducer(), geometry=region, scale=20, maxPixels=1e9, bestEffort=True, tileScale=4,
            ).getInfo()
            clipped = composite.select(union).gt(COMPOSITE_DISPLAY_MAX).reduceRegion(
                reducer=ee.Reducer.mean(), geometry=region, scale=20, maxPixels=1e9, bestEffort=True, tileScale=4,
            ).getInfo()

            for cid in optical_ids:
                spec = SPECTRAL_COMPOSITES[cid]
                channels = []
                for chan, band in zip(("R", "G", "B"), spec["bands"]):
                    channels.append({
                        "channel": chan, "band": band, "name": S2_BAND_NAMES[band],
                        "mean": round(stats.get(f"{band}_mean", 0) or 0, 4),
                        "std_dev": round(stats.get(f"{band}_stdDev", 0) or 0, 4),
                        "pct_clipped_by_display": round(100 * (clipped.get(band, 0) or 0), 2),
                    })
                vis = {"bands": spec["bands"], "min": COMPOSITE_DISPLAY_MIN, "max": COMPOSITE_DISPLAY_MAX}
                built[cid] = {
                    "label": spec["label"], "sensor": "Sentinel-2 SR Harmonized", "reveals": spec["reveals"],
                    "citation": spec["citation"], "notes": spec["notes"], "channels": channels,
                    "display_stretch": f"{COMPOSITE_DISPLAY_MIN}–{COMPOSITE_DISPLAY_MAX} surface reflectance (fixed, same for every optical composite)",
                    "map_layer": _tile_layer(composite, vis),
                    "download_url": _download_url(composite.select(spec["bands"]), region, scale=20),
                    "download_note": "Raw surface-reflectance bands in R, G, B channel order (not the stretched picture).",
                }

    if "sar_composite" in composites:
        spec = SPECTRAL_COMPOSITES["sar_composite"]
        try:
            s1 = _s1_composite_image(region, start_date, end_date)
        except ValueError as e:
            unavailable["sar_composite"] = str(e)
        else:
            img = s1["image"]
            stats = img.reduceRegion(
                reducer=_mean_std_reducer(), geometry=region, scale=20, maxPixels=1e9, bestEffort=True, tileScale=4,
            ).getInfo()
            valid_px = img.select("VV").mask().reduceRegion(
                reducer=ee.Reducer.mean(), geometry=region, scale=20, maxPixels=1e9, bestEffort=True, tileScale=4,
            ).getInfo()
            names = {"VV": "VV backscatter (dB)", "VH": "VH backscatter (dB)", "VV_minus_VH": "VV − VH (dB)"}
            channels = [{
                "channel": chan, "band": band, "name": names[band],
                "mean": round(stats.get(f"{band}_mean", 0) or 0, 2),
                "std_dev": round(stats.get(f"{band}_stdDev", 0) or 0, 2),
            } for chan, band in zip(("R", "G", "B"), spec["bands"])]
            vis = {"bands": spec["bands"],
                   "min": [SAR_COMPOSITE_DB_MIN, SAR_COMPOSITE_DB_MIN, SAR_RATIO_DISPLAY_MIN],
                   "max": [SAR_COMPOSITE_DB_MAX, SAR_COMPOSITE_DB_MAX, SAR_RATIO_DISPLAY_MAX]}
            built["sar_composite"] = {
                "label": spec["label"], "sensor": "Sentinel-1 GRD (IW, dual-pol)", "reveals": spec["reveals"],
                "citation": spec["citation"], "notes": spec["notes"], "channels": channels,
                "display_stretch": (f"VV, VH: {SAR_COMPOSITE_DB_MIN:g} to {SAR_COMPOSITE_DB_MAX:g} dB; "
                                    f"VV−VH: {SAR_RATIO_DISPLAY_MIN:g} to {SAR_RATIO_DISPLAY_MAX:g} dB (ESTIMATE, display only)"),
                "scene_count": s1["scene_count"], "orbit_pass": s1["orbit"],
                "scenes_ascending": s1["scenes_ascending"], "scenes_descending": s1["scenes_descending"],
                "valid_pixel_fraction": round((valid_px.get("VV", 0) or 0), 4),
                "scene_provenance": _s1_scene_list(s1["collection"]),
                "map_layer": _tile_layer(img, vis),
                "download_url": _download_url(img, region, scale=20),
                "download_note": "Raw VV, VH and VV−VH bands in dB, R, G, B channel order (not the stretched picture).",
            }

    if not built:
        raise ValueError("No composite could be built: " + "; ".join(f"{k}: {v}" for k, v in unavailable.items()))

    return {
        "composites": {c: built[c] for c in composites if c in built},
        "unavailable": unavailable,
        "method": (
            "Band-combination composites of Sentinel-2 SR Harmonized (cloud/shadow/cirrus-masked via the Scene "
            "Classification Layer, median composite"
            + (f" of {scene_count} scene(s)" if scene_count else "")
            + ", 10-20m native resolution) and Sentinel-1 GRD IW dual-pol (single-orbit-pass median, dB). No new science: "
            "each recipe is a published band combination (see each composite's citation). Colours are a fixed display "
            "stretch, not a measurement — use the per-channel statistics and the raw-band GeoTIFFs for anything quantitative."
        ),
        "scene_count": scene_count,
        "valid_pixel_fraction": valid_pixel_fraction,
        "scene_provenance": scene_provenance,
        "aoi_area_km2": round(_region_area_km2(region), 3),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Vegetation drought index — VCI / TCI / VHI (Kogan 1990, 1995). Each pixel's
# NDVI (VCI) and daytime land-surface temperature (TCI) for the chosen window
# is rescaled 0-100 against that SAME pixel's min/max for the SAME calendar
# window across all earlier years, then combined: VHI = 0.5*VCI + 0.5*TCI.
#
# Verified: MODIS/061/MOD13Q1 (NDVI, scale 0.0001, 250 m, 16-day, from
# 2000-02-18, SummaryQA 0 good / 1 marginal / 2 snow-ice / 3 cloudy) and
# MODIS/061/MOD11A2 (LST_Day_1km, scale 0.02 K, 1 km, 8-day) against the Earth
# Engine catalog. The VHI formula and the 0.5/0.5 weighting were checked
# against secondary sources only (Kogan's papers were not retrieved), as were
# the drought-class cut-offs below (Kogan 2002 as reproduced in a MODIS
# agricultural-drought method summary) — treat the class table as unverified
# against the primary paper.
# ═════════════════════════════════════════════════════════════════════════════

VHI_ALPHA = 0.5  # VHI = alpha*VCI + (1-alpha)*TCI; 0.5/0.5 per secondary sources
DROUGHT_BASELINE_FIRST_YEAR = 2001  # first year with a full MOD13Q1/MOD11A2 calendar year (record starts 2000-02-18)
MIN_BASELINE_YEARS = 2  # mathematical minimum for max != min; NOT a quality threshold — see 'baseline' in the result
LST_VALID_MIN = 7500  # catalog's documented minimum LST_Day_1km DN (fill value is below it)
DROUGHT_SCALE_M = 1000  # LST native 1 km is the coarser input
# (code, label, upper bound inclusive) — evaluated from the driest class up
VHI_DROUGHT_CLASSES = [
    (0, "Extreme drought", 10), (1, "Severe drought", 20), (2, "Moderate drought", 30),
    (3, "Mild drought", 40), (4, "No drought", 100),
]
VHI_CLASS_PALETTE = ["#7f0000", "#d7301f", "#fc8d59", "#fdd49e", "#1a9850"]


def _md_clamped(d):
    """(month, day) with Feb 29 -> Feb 28 so the same window exists in every year."""
    return (2, 28) if (d.month, d.day) == (2, 29) else (d.month, d.day)


def _baseline_min_max(col, band, m1, d1, m2, d2, y0, y1):
    """Per-pixel min and max over years y0..y1 of the window's mean `band`,
    plus the number of years that actually had imagery over the AOI."""
    def _year(y):
        y = ee.Number(y).int()
        s = ee.Date.fromYMD(y, m1, d1)
        e = ee.Date.fromYMD(y, m2, d2).advance(1, "day")
        sub = col.filterDate(s, e)
        empty = ee.Image(0).rename(band).updateMask(ee.Image(0))
        img = ee.Image(ee.Algorithms.If(sub.size().gt(0), sub.mean().select(band), empty))
        return img.set("n", sub.size()).set("year", y)
    yearly = ee.ImageCollection.fromImages(ee.List.sequence(y0, y1).map(_year))
    return yearly.min(), yearly.max(), yearly


def _build_vhi_stack(region: ee.Geometry, start_date: str, end_date: str) -> Dict[str, Any]:
    """Server-side VCI/TCI/VHI image stack plus the counts needed to report
    how much data backed it. Shared by the compute function and the PDF
    thumbnail so both use identical logic."""
    import datetime as _dt
    _validate_date_range(start_date, end_date)
    s, e = (_dt.date.fromisoformat(start_date), _dt.date.fromisoformat(end_date))
    if s.year != e.year:
        raise ValueError("vegetation_drought_index needs a window inside a single calendar year (the baseline is the same window in earlier years).")
    year = s.year
    if year - DROUGHT_BASELINE_FIRST_YEAR < MIN_BASELINE_YEARS:
        raise ValueError(f"Need at least {MIN_BASELINE_YEARS} earlier baseline years from {DROUGHT_BASELINE_FIRST_YEAR}; choose {DROUGHT_BASELINE_FIRST_YEAR + MIN_BASELINE_YEARS} or later.")
    (m1, d1), (m2, d2) = _md_clamped(s), _md_clamped(e)

    def _ndvi(img):
        qa = img.select("SummaryQA")
        return img.select("NDVI").multiply(0.0001).updateMask(qa.lte(1)).rename("NDVI")  # 0 good, 1 marginal

    def _lst(img):
        raw = img.select("LST_Day_1km")
        return raw.updateMask(raw.gte(LST_VALID_MIN)).multiply(0.02).rename("LST")  # Kelvin

    ndvi_col = ee.ImageCollection("MODIS/061/MOD13Q1").filterBounds(region).map(_ndvi)
    lst_col = ee.ImageCollection("MODIS/061/MOD11A2").filterBounds(region).map(_lst)

    s_ee, e_ee = ee.Date(start_date), _cap_end_date(end_date).advance(1, "day")
    n_target, l_target = ndvi_col.filterDate(s_ee, e_ee), lst_col.filterDate(s_ee, e_ee)
    if n_target.size().getInfo() == 0 or l_target.size().getInfo() == 0:
        raise ValueError(f"No MODIS NDVI/LST composites found for {start_date} - {end_date}. Try a wider window or earlier dates.")
    ndvi_t, lst_t = n_target.mean(), l_target.mean()

    y0, y1 = DROUGHT_BASELINE_FIRST_YEAR, year - 1
    ndvi_min, ndvi_max, n_years_col = _baseline_min_max(ndvi_col, "NDVI", m1, d1, m2, d2, y0, y1)
    lst_min, lst_max, l_years_col = _baseline_min_max(lst_col, "LST", m1, d1, m2, d2, y0, y1)
    ndvi_years = sum(1 for n in n_years_col.aggregate_array("n").getInfo() if n)
    lst_years = sum(1 for n in l_years_col.aggregate_array("n").getInfo() if n)
    if min(ndvi_years, lst_years) < MIN_BASELINE_YEARS:
        raise ValueError(f"Only {min(ndvi_years, lst_years)} baseline year(s) have MODIS data for this window/AOI; need at least {MIN_BASELINE_YEARS}.")

    # Pixels where baseline max == min have no range, so the index is undefined there (masked, not zero-filled).
    n_rng, l_rng = ndvi_max.subtract(ndvi_min), lst_max.subtract(lst_min)
    vci = ndvi_t.subtract(ndvi_min).divide(n_rng.updateMask(n_rng.gt(0))).multiply(100).rename("VCI")
    tci = lst_max.subtract(lst_t).divide(l_rng.updateMask(l_rng.gt(0))).multiply(100).rename("TCI")
    vhi = vci.multiply(VHI_ALPHA).add(tci.multiply(1 - VHI_ALPHA)).rename("VHI")
    stack = vci.addBands(tci).addBands(vhi)
    return {"stack": stack, "vhi": vhi, "year": year, "y0": y0, "y1": y1, "m1": m1, "d1": d1, "m2": m2, "d2": d2,
            "ndvi_years": ndvi_years, "lst_years": lst_years,
            "ndvi_composites_used": n_target.size().getInfo(), "lst_composites_used": l_target.size().getInfo()}


def compute_vegetation_drought_index(aoi: Dict, start_date: str, end_date: str) -> Dict[str, Any]:
    """VCI, TCI and VHI (Kogan 1990/1995) for a window inside one calendar
    year, against a per-pixel baseline of the same calendar window in
    2001..(year-1). MODIS NDVI (250 m) and daytime LST (1 km), analysed at 1 km."""
    logger.info(f"GEE (remote sensing): vegetation_drought_index {start_date} -> {end_date}")
    region = _polygon_geometry(aoi)
    b = _build_vhi_stack(region, start_date, end_date)
    stack, vhi = b["stack"], b["vhi"]
    y0, y1, m1, d1, m2, d2 = b["y0"], b["y1"], b["m1"], b["d1"], b["m2"], b["d2"]
    ndvi_years, lst_years = b["ndvi_years"], b["lst_years"]

    reducer = ee.Reducer.mean().combine(ee.Reducer.minMax(), sharedInputs=True).combine(ee.Reducer.stdDev(), sharedInputs=True)
    kw = dict(geometry=region, scale=DROUGHT_SCALE_M, maxPixels=1e9, bestEffort=True, tileScale=4)
    st = stack.reduceRegion(reducer=reducer, **kw).getInfo()
    valid = vhi.mask().reduceRegion(reducer=ee.Reducer.mean(), **kw).getInfo()

    cls = ee.Image(4)
    for code, _label, upper in VHI_DROUGHT_CLASSES[:-1][::-1]:
        cls = cls.where(vhi.lte(upper), code)
    cls = cls.updateMask(vhi.mask()).rename("class")
    hist = cls.reduceRegion(reducer=ee.Reducer.frequencyHistogram(), **kw).getInfo().get("class") or {}
    total = sum(hist.values()) or 1
    classes = [{
        "code": code, "label": label, "vhi_range": (f"VHI ≤ {upper}" if code == 0 else f"{VHI_DROUGHT_CLASSES[code-1][2]} < VHI ≤ {upper}" if upper < 100 else f"VHI > {VHI_DROUGHT_CLASSES[code-1][2]}"),
        "pct_of_valid_pixels": round(100 * (hist.get(str(code), 0) or 0) / total, 2),
    } for code, label, upper in VHI_DROUGHT_CLASSES]

    palettes = {
        "VCI": {"min": 0, "max": 100, "palette": ["#7f0000", "#fdd49e", "#ffffbf", "#a6d96a", "#1a9850"]},
        "TCI": {"min": 0, "max": 100, "palette": ["#7f0000", "#fdd49e", "#ffffbf", "#a6d96a", "#1a9850"]},
        "VHI": {"min": 0, "max": 100, "palette": ["#7f0000", "#d7301f", "#fc8d59", "#fdd49e", "#ffffbf", "#a6d96a", "#1a9850"]},
    }
    meta = {
        "VCI": ("VCI — Vegetation Condition Index", "100 × (NDVI − NDVImin) / (NDVImax − NDVImin)", "Kogan 1990, 1995", "Where this window's NDVI sits between the driest-looking and greenest this pixel has been in the same season across the baseline years. Low = poor vegetation for the time of year."),
        "TCI": ("TCI — Temperature Condition Index", "100 × (LSTmax − LST) / (LSTmax − LSTmin)", "Kogan 1995", "Same idea for daytime land-surface temperature, inverted so hotter than usual scores low. Low = thermal stress."),
        "VHI": ("VHI — Vegetation Health Index", f"{VHI_ALPHA} × VCI + {1 - VHI_ALPHA} × TCI", "Kogan 1995, 2001", "Combined moisture + thermal stress. Low values indicate agricultural drought stress; classes below."),
    }
    indices = {}
    for key in ("VCI", "TCI", "VHI"):
        label, formula, cite, interp = meta[key]
        indices[key.lower()] = {
            "label": label, "formula": formula, "citation": cite, "interpretation": interp,
            "mean": round(st.get(f"{key}_mean", 0) or 0, 2), "min": round(st.get(f"{key}_min", 0) or 0, 2),
            "max": round(st.get(f"{key}_max", 0) or 0, 2), "std_dev": round(st.get(f"{key}_stdDev", 0) or 0, 2),
            "map_layer": _tile_layer(stack.select(key), palettes[key]),
        }
    return {
        "indices": indices,
        "drought_classes": classes,
        "class_note": "Class cut-offs (Kogan 2002, via a secondary MODIS drought-method summary) were not checked against the primary paper.",
        "baseline": {"years_requested": f"{y0}–{y1}", "ndvi_years_with_data": ndvi_years, "lst_years_with_data": lst_years,
                     "window": f"{m1:02d}-{d1:02d} to {m2:02d}-{d2:02d} each year",
                     "caveat": "VCI/TCI are relative to this baseline; short records understate extremes. Pixels with no baseline range are masked."},
        "valid_pixel_fraction": round((valid.get("VHI", 0) or 0), 4),
        "ndvi_composites_used": b["ndvi_composites_used"], "lst_composites_used": b["lst_composites_used"],
        "download_url": _download_url(stack, region, scale=DROUGHT_SCALE_M),
        "download_note": "3-band GeoTIFF: VCI, TCI, VHI (0–100) at 1 km.",
        "method": (
            "Kogan's VCI/TCI/VHI from MODIS/061/MOD13Q1 NDVI (250 m, quality-masked to SummaryQA 0–1: good or marginal) and "
            "MODIS/061/MOD11A2 daytime LST (1 km), analysed at 1 km. Target window mean vs per-pixel min/max of the same calendar "
            f"window in {y0}–{y1}. VHI = {VHI_ALPHA}·VCI + {1 - VHI_ALPHA}·TCI. Relative index: it says how stressed vegetation is "
            "versus this pixel's own history, not an absolute drought measure. Documented-event validation still pending."
        ),
        "aoi_area_km2": round(_region_area_km2(region), 3),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Night lights — monthly VIIRS Day/Night Band radiance as a proxy for
# lit human activity (an economic-activity PROXY, not a measurement of GDP).
#
# Verified against the Earth Engine catalog: NOAA/VIIRS/DNB/MONTHLY_V1/VCMSLCFG
# (stray-light-corrected monthly composites, 2014-01 onward), bands avg_rad
# (nW/sr/cm^2) and cf_cvg (number of cloud-free observations), 463.83 m. The
# catalog states v1 is NOT filtered: aurora, fires, boats and other
# temporary lights are included, and a zero avg_rad does not mean no lights
# were seen — check cf_cvg. The 0-60 display range is the catalog's own
# sample stretch.
# ═════════════════════════════════════════════════════════════════════════════

VIIRS_MONTHLY_ID = "NOAA/VIIRS/DNB/MONTHLY_V1/VCMSLCFG"
VIIRS_SCALE_M = 463.83
VIIRS_DISPLAY_MIN, VIIRS_DISPLAY_MAX = 0, 60  # catalog sample stretch (display only)
NIGHT_LIGHTS_PALETTE = ["#000000", "#3b2a6b", "#c2453a", "#ffd24d", "#ffffff"]


def compute_night_lights(aoi: Dict, start_date: str, end_date: str) -> Dict[str, Any]:
    """Monthly mean VIIRS DNB radiance (and summed radiance) over the AOI for
    each month in the window, with the cloud-free-observation count behind each
    month and a Mann-Kendall / Sen's slope trend. Months with no composite or
    with zero cloud-free observations over the AOI are reported as gaps."""
    logger.info(f"GEE (remote sensing): night_lights {start_date} -> {end_date}")
    _validate_date_range(start_date, end_date)
    _require_start_after(start_date, "2014-01-01", "VIIRS DNB stray-light-corrected monthly")
    region = _polygon_geometry(aoi)

    col = (
        ee.ImageCollection(VIIRS_MONTHLY_ID).filterBounds(region)
        .filterDate(ee.Date(start_date), _cap_end_date(end_date).advance(1, "day"))
        .select(["avg_rad", "cf_cvg"])
    )
    if col.size().getInfo() == 0:
        raise ValueError(f"No VIIRS monthly composites found for {start_date} - {end_date}. Try an earlier window (the product lags real time).")

    reducer = ee.Reducer.mean().combine(ee.Reducer.sum(), sharedInputs=True).combine(ee.Reducer.stdDev(), sharedInputs=True)

    def _month(img):
        stats = img.reduceRegion(reducer=reducer, geometry=region, scale=VIIRS_SCALE_M, maxPixels=1e9, bestEffort=True, tileScale=4)
        return ee.Feature(None, stats).set("date", img.date().format("YYYY-MM-dd"))

    feats = ee.FeatureCollection(col.map(_month)).getInfo().get("features", [])
    by_date = {f["properties"].get("date"): f["properties"] for f in feats}

    points = []
    for d in sorted(by_date):
        p = by_date[d]
        mean, cf = p.get("avg_rad_mean"), p.get("cf_cvg_mean")
        usable = mean is not None and (cf or 0) > 0  # zero cloud-free observations = no information, not "no lights"
        points.append({
            "date": d,
            "value": round(mean, 4) if usable else None,
            "std_dev": round(p.get("avg_rad_stdDev") or 0, 4) if usable else None,
            "sum_of_lights": round(p.get("avg_rad_sum") or 0, 2) if usable else None,
            "mean_cf_cvg": round(cf, 2) if cf is not None else None,
        })

    valid = [(p["date"], p["value"]) for p in points if p["value"] is not None]
    trend_analysis = (
        mann_kendall_test([v for _, v in valid], [d_ for d_, _ in valid]) if len(valid) >= trend_stats.MIN_POINTS
        else {"status": "insufficient_data", "note": f"Only {len(valid)} usable months — Mann-Kendall needs at least {trend_stats.MIN_POINTS}."}
    )

    mean_img = col.select("avg_rad").mean()
    return {
        "points": points,
        "months_in_window": len(points), "usable_months": len(valid),
        "trend_analysis": trend_analysis,
        "mean_radiance_overall": round(sum(v for _, v in valid) / len(valid), 4) if valid else None,
        "units": "nW/sr/cm² (avg_rad); sum_of_lights = radiance summed over AOI pixels (a relative index, depends on AOI size)",
        "display_stretch": f"{VIIRS_DISPLAY_MIN}–{VIIRS_DISPLAY_MAX} nW/sr/cm² (catalog sample stretch, display only)",
        "map_layer": _tile_layer(mean_img, {"min": VIIRS_DISPLAY_MIN, "max": VIIRS_DISPLAY_MAX, "palette": NIGHT_LIGHTS_PALETTE}),
        "download_url": _download_url(col.select(["avg_rad", "cf_cvg"]).mean(), region, scale=int(VIIRS_SCALE_M)),
        "download_note": "Window-mean avg_rad and cf_cvg at ~464 m.",
        "caveats": [
            "Night lights are a proxy for lit activity, not a direct measure of economic output.",
            "This product version is unfiltered: fires, boats, aurora and other temporary lights are included.",
            "Months with zero cloud-free observations over the AOI are shown as gaps, not as darkness.",
            "The monthly series has a seasonal cycle and serial correlation; Mann-Kendall here ignores both, so treat p-values as indicative.",
        ],
        "method": (
            f"{VIIRS_MONTHLY_ID} (VIIRS Day/Night Band monthly average radiance, stray-light corrected, ~464 m, from 2014-01). "
            "AOI mean, std dev and sum of avg_rad per month, with the mean cloud-free observation count (cf_cvg) behind each month; "
            "Mann-Kendall test and Sen's slope on the usable months."
        ),
        "aoi_area_km2": round(_region_area_km2(region), 3),
    }


# ═════════════════════════════════════════════════════════════════════════════
# GEDI forest structure — canopy height (rh98) and aboveground biomass
# density (AGBD) from spaceborne lidar footprints (carbon / forest story).
#
# Verified against the Earth Engine catalog (GEDI is sampled along orbit
# tracks: ~25 m footprints every ~60 m along-track, NOT wall-to-wall, and only
# between 51.6 N and 51.6 S):
#   LARSE/GEDI/GEDI02_A_002_MONTHLY — rh98 (m); catalog quality mask:
#       quality_flag == 1 AND degrade_flag == 0; catalog sample stretch 1-60 m.
#   LARSE/GEDI/GEDI04_A_002_MONTHLY — agbd (Mg/ha), agbd_se (Mg/ha);
#       catalog quality mask: l4_quality_flag == 1 AND degrade_flag == 0.
# Monthly rasters are composites of individual orbits; availability starts
# 2019-03-25.
# What this deliberately does NOT do: multiply a footprint mean by the AOI area
# to claim a total biomass or carbon stock. GEDI's footprint sample is not a
# probability sample of the AOI; the gridded L4B product (LARSE/GEDI/
# GEDI04_B_002) is the product built for area-level estimates and was not
# integrated here (its bands were not verified).
# ═════════════════════════════════════════════════════════════════════════════

GEDI_L2A_MONTHLY = "LARSE/GEDI/GEDI02_A_002_MONTHLY"
GEDI_L4A_MONTHLY = "LARSE/GEDI/GEDI04_A_002_MONTHLY"
GEDI_FIRST_DATE = "2019-03-25"
GEDI_SCALE_M = 25
GEDI_RH_DISPLAY_MIN, GEDI_RH_DISPLAY_MAX = 1, 60  # catalog sample stretch for rh98 (display only)
GEDI_HEIGHT_PALETTE = ["#8b0000", "#ff0000", "#ffa500", "#008000", "#006400"]  # darkred,red,orange,green,darkgreen (catalog sample)
GEDI_AGBD_PALETTE = ["#f7fcb9", "#addd8e", "#31a354", "#006837"]


def _gedi_collection(col_id, qa_band, bands, region, start_date, end_date):
    """Quality-masked monthly GEDI rasters for the window, using the catalog's own mask."""
    def _mask(im):
        return im.updateMask(im.select(qa_band).eq(1)).updateMask(im.select("degrade_flag").eq(0))
    return (
        ee.ImageCollection(col_id).filterBounds(region)
        .filterDate(ee.Date(start_date), _cap_end_date(end_date).advance(1, "day"))
        .map(_mask).select(bands)
    )


def _gedi_stats(img, band, region):
    """Footprint statistics for one band of a masked composite."""
    reducer = (ee.Reducer.mean().combine(ee.Reducer.stdDev(), sharedInputs=True)
               .combine(ee.Reducer.count(), sharedInputs=True)
               .combine(ee.Reducer.percentile([10, 50, 90]), sharedInputs=True))
    return img.select(band).reduceRegion(
        reducer=reducer, geometry=region, scale=GEDI_SCALE_M, maxPixels=1e10, bestEffort=True, tileScale=4,
    ).getInfo()


def _r(v, nd=2):
    return None if v is None else round(v, nd)


def compute_gedi_forest_structure(aoi: Dict, start_date: str, end_date: str) -> Dict[str, Any]:
    """GEDI canopy height (rh98) and aboveground biomass density for footprints
    inside the AOI over the window, quality-filtered with the catalog's masks.
    Reports footprint statistics, footprint counts and density (how much data
    backs the numbers), the biomass model's own standard error, and pixel-level
    layers. Footprint-level statistics only: no area totals (see module note)."""
    logger.info(f"GEE (remote sensing): gedi_forest_structure {start_date} -> {end_date}")
    _validate_date_range(start_date, end_date)
    _require_start_after(start_date, GEDI_FIRST_DATE, "GEDI")
    region = _polygon_geometry(aoi)
    area_km2 = round(_region_area_km2(region), 3)

    h_col = _gedi_collection(GEDI_L2A_MONTHLY, "quality_flag", ["rh98"], region, start_date, end_date)
    b_col = _gedi_collection(GEDI_L4A_MONTHLY, "l4_quality_flag", ["agbd", "agbd_se"], region, start_date, end_date)
    n_h, n_b = h_col.size().getInfo(), b_col.size().getInfo()
    if n_h == 0 and n_b == 0:
        raise ValueError(
            f"No GEDI monthly rasters intersect this AOI for {start_date} - {end_date}. GEDI covers 51.6°N–51.6°S, "
            f"started {GEDI_FIRST_DATE}, and the catalog's coverage ends around 2025; try a wider window."
        )

    # Per-pixel mean over the window: a pixel is one 25 m footprint (where footprints from different orbits fall
    # on the same pixel they are averaged).
    out: Dict[str, Any] = {"canopy_height": None, "biomass": None}
    layers_for_download = []

    if n_h:
        h_img = h_col.mean()
        st = _gedi_stats(h_img, "rh98", region)
        n = int(st.get("rh98_count") or 0)
        if n:
            out["canopy_height"] = {
                "metric": "rh98 (relative height at 98% of the GEDI waveform; the catalog titles this product 'Canopy Top Height')",
                "units": "m", "footprints": n, "footprints_per_km2": _r(n / area_km2, 2) if area_km2 else None,
                "mean": _r(st.get("rh98_mean")), "std_dev": _r(st.get("rh98_stdDev")),
                "p10": _r(st.get("rh98_p10")), "p50": _r(st.get("rh98_p50")), "p90": _r(st.get("rh98_p90")),
                "monthly_rasters_used": n_h,
                "display_stretch": f"{GEDI_RH_DISPLAY_MIN}–{GEDI_RH_DISPLAY_MAX} m (catalog sample stretch, display only)",
                "map_layer": _tile_layer(h_img, {"bands": ["rh98"], "min": GEDI_RH_DISPLAY_MIN, "max": GEDI_RH_DISPLAY_MAX, "palette": GEDI_HEIGHT_PALETTE}),
                "citation": "GEDI L2A Geolocated Elevation and Height Metrics, Version 2 (NASA LP DAAC), via LARSE/Google monthly rasters; quality mask per Earth Engine catalog.",
            }
            layers_for_download.append(h_img)

    if n_b:
        b_img = b_col.mean()
        st = _gedi_stats(b_img, "agbd", region)
        n = int(st.get("agbd_count") or 0)
        if n:
            se = b_img.select("agbd_se").reduceRegion(reducer=ee.Reducer.mean(), geometry=region, scale=GEDI_SCALE_M,
                                                     maxPixels=1e10, bestEffort=True, tileScale=4).getInfo()
            p10, p90 = st.get("agbd_p10"), st.get("agbd_p90")
            sd = st.get("agbd_stdDev")
            out["biomass"] = {
                "metric": "agbd (predicted aboveground biomass density, per footprint)",
                "units": "Mg/ha", "footprints": n, "footprints_per_km2": _r(n / area_km2, 2) if area_km2 else None,
                "mean": _r(st.get("agbd_mean")), "std_dev": _r(sd),
                "p10": _r(p10), "p50": _r(st.get("agbd_p50")), "p90": _r(p90),
                "mean_prediction_se": _r(se.get("agbd_se")),
                "naive_standard_error_of_mean": _r(sd / (n ** 0.5)) if (sd is not None and n > 1) else None,
                "monthly_rasters_used": n_b,
                "display_stretch": f"{_r(p10, 1)}–{_r(p90, 1)} Mg/ha (AOI 10th–90th percentile of footprints, display only)",
                "map_layer": _tile_layer(b_img, {"bands": ["agbd"], "min": p10 if p10 is not None else 0, "max": p90 if p90 else 1, "palette": GEDI_AGBD_PALETTE}),
                "citation": "GEDI L4A Footprint Level Aboveground Biomass Density, Version 2.1 (Dubayah et al., ORNL DAAC), via LARSE/Google monthly rasters; quality mask per Earth Engine catalog.",
            }
            layers_for_download.append(b_img)

    if not out["canopy_height"] and not out["biomass"]:
        raise ValueError(f"GEDI rasters exist near this AOI but no quality-passing footprints fall inside it for {start_date} - {end_date}. Try a wider window or a larger AOI.")

    stack = layers_for_download[0]
    for extra in layers_for_download[1:]:
        stack = stack.addBands(extra)
    return {
        **out,
        "quality_filter": "Catalog masks: L2A quality_flag = 1 and degrade_flag = 0; L4A l4_quality_flag = 1 and degrade_flag = 0.",
        "download_url": _download_url(stack, region, scale=GEDI_SCALE_M),
        "download_note": "Window-mean footprint pixels (25 m): rh98 (m), agbd and agbd_se (Mg/ha), sparse along orbit tracks.",
        "caveats": [
            "GEDI samples along orbit tracks (~25 m footprints), not the whole AOI — read the footprint count and density before trusting a mean.",
            "Statistics describe the sampled footprints only. No area total or carbon stock is computed: footprints are not a probability sample of the AOI.",
            "AGBD is a model prediction (per-footprint standard error reported); it is not a field measurement and is least reliable outside the forest types the model was calibrated on.",
            "The naive standard error of the mean assumes independent footprints; neighbouring footprints are spatially correlated, so it understates true uncertainty.",
            "No leaf-on / leaf-off or sensitivity filtering beyond the catalog's quality masks is applied.",
        ],
        "method": (
            "GEDI LiDAR footprints from the LARSE/Google monthly rasters of L2A (rh98 canopy height) and L4A (agbd, agbd_se), "
            "filtered with the Earth Engine catalog's own quality masks, averaged per pixel over the window, and summarised "
            "over the AOI at 25 m (mean, std dev, 10th/50th/90th percentile, footprint count)."
        ),
        "aoi_area_km2": area_km2,
    }


# ═════════════════════════════════════════════════════════════════════════════
# Timelapse — one natural-colour frame per calendar year from Landsat 5/7/8/9
# Collection 2 Level-2 surface reflectance, cloud-masked and median-composited,
# with per-frame scene count, valid-pixel fraction and mean NDVI so the
# animation comes with numbers rather than just pictures.
#
# Verified (Earth Engine catalog / Landsat C2 documentation as reproduced in
# several sources): collections LANDSAT/LT05|LE07|LC08|LC09/C02/T1_L2; surface
# reflectance = DN * 0.0000275 - 0.2; QA_PIXEL bits 0 fill, 1 dilated cloud,
# 3 cloud, 4 cloud shadow (bit 5 snow is deliberately NOT masked so Himalayan
# snow stays visible); TM/ETM+ blue,green,red,NIR = SR_B1..SR_B4; OLI
# (L8/L9) = SR_B2..SR_B5.
# Not verified here: exact first-available date of the L5 C2 L2 collection
# (the 1984 floor below is Landsat 5's launch year; frames with no data are
# simply dropped) and the lifetime of generated thumbnail URLs.
# ═════════════════════════════════════════════════════════════════════════════

LANDSAT_C2_SENSORS = [
    {"id": "LANDSAT/LT05/C02/T1_L2", "name": "Landsat 5 TM", "bands": ["SR_B1", "SR_B2", "SR_B3", "SR_B4"]},
    {"id": "LANDSAT/LE07/C02/T1_L2", "name": "Landsat 7 ETM+", "bands": ["SR_B1", "SR_B2", "SR_B3", "SR_B4"]},
    {"id": "LANDSAT/LC08/C02/T1_L2", "name": "Landsat 8 OLI", "bands": ["SR_B2", "SR_B3", "SR_B4", "SR_B5"]},
    {"id": "LANDSAT/LC09/C02/T1_L2", "name": "Landsat 9 OLI-2", "bands": ["SR_B2", "SR_B3", "SR_B4", "SR_B5"]},
]
TIMELAPSE_FIRST_YEAR = 1984  # Landsat 5 launch year; years without data are dropped
TIMELAPSE_MAX_FRAMES = 30
TIMELAPSE_MIN_FRAMES = 2
TIMELAPSE_SCALE_M = 30
TIMELAPSE_THUMB_PX = 512
TIMELAPSE_DISPLAY_MIN, TIMELAPSE_DISPLAY_MAX = 0.0, 0.3  # same fixed reflectance stretch as the Sentinel-2 composites (display only)


def _landsat_merged(region, first_year: int, last_year: int):
    """Cloud-masked, scaled Landsat 5/7/8/9 C2 SR with common band names blue/green/red/nir."""
    s = ee.Date.fromYMD(first_year, 1, 1)
    e = ee.Date.fromYMD(last_year, 12, 31).advance(1, "day")
    merged = None
    for sensor in LANDSAT_C2_SENSORS:
        def _prep(img, _bands=sensor["bands"]):
            qa = img.select("QA_PIXEL")
            ok = (qa.bitwiseAnd(1).eq(0).And(qa.bitwiseAnd(1 << 1).eq(0))
                  .And(qa.bitwiseAnd(1 << 3).eq(0)).And(qa.bitwiseAnd(1 << 4).eq(0)))
            sr = img.select(_bands).multiply(0.0000275).add(-0.2).rename(["blue", "green", "red", "nir"])
            return sr.updateMask(ok).copyProperties(img, ["system:time_start"])
        col = ee.ImageCollection(sensor["id"]).filterBounds(region).filterDate(s, e).map(_prep)
        merged = col if merged is None else merged.merge(col)
    return merged


def _year_composite(merged, year: int):
    s = ee.Date.fromYMD(year, 1, 1)
    return merged.filterDate(s, s.advance(1, "year")).median()


def compute_timelapse(aoi: Dict, start_date: str, end_date: str) -> Dict[str, Any]:
    """Annual Landsat timelapse (one cloud-masked median frame per calendar
    year from start_date's year to end_date's year; the last year may be
    partial). Returns per-frame thumbnail URLs for client-side playback plus
    per-frame scene count, valid-pixel fraction and AOI-mean NDVI, and a
    Mann-Kendall trend on the NDVI series."""
    import datetime as _dt
    from concurrent.futures import ThreadPoolExecutor
    logger.info(f"GEE (remote sensing): timelapse {start_date} -> {end_date}")
    _validate_date_range(start_date, end_date)
    y0, y1 = _dt.date.fromisoformat(start_date).year, _dt.date.fromisoformat(end_date).year
    if y0 < TIMELAPSE_FIRST_YEAR:
        raise ValueError(f"Landsat Collection 2 imagery starts in {TIMELAPSE_FIRST_YEAR} (Landsat 5); choose a later start.")
    n_years = y1 - y0 + 1
    if n_years < TIMELAPSE_MIN_FRAMES:
        raise ValueError(f"A timelapse needs at least {TIMELAPSE_MIN_FRAMES} calendar years; widen the date range.")
    if n_years > TIMELAPSE_MAX_FRAMES:
        raise ValueError(f"At most {TIMELAPSE_MAX_FRAMES} yearly frames per timelapse; narrow the date range (got {n_years}).")
    region = _polygon_geometry(aoi)
    merged = _landsat_merged(region, y0, y1)

    empty = ee.Image.constant([0, 0, 0, 0]).rename(["blue", "green", "red", "nir"]).updateMask(ee.Image(0))

    def _year_stats(y):
        y = ee.Number(y).int()
        s = ee.Date.fromYMD(y, 1, 1)
        sub = merged.filterDate(s, s.advance(1, "year"))
        n = sub.size()
        comp = ee.Image(ee.Algorithms.If(n.gt(0), sub.median(), empty))
        both = comp.normalizedDifference(["nir", "red"]).rename("ndvi").addBands(comp.select("red").mask().rename("valid"))
        stats = both.reduceRegion(reducer=ee.Reducer.mean(), geometry=region, scale=TIMELAPSE_SCALE_M, maxPixels=1e9, bestEffort=True, tileScale=4)
        return ee.Feature(None, stats).set("year", y).set("n", n)

    feats = ee.FeatureCollection(ee.List.sequence(y0, y1).map(_year_stats)).getInfo().get("features", [])
    rows = sorted((f["properties"] for f in feats), key=lambda p: p["year"])
    usable = [p for p in rows if (p.get("n") or 0) > 0 and (p.get("valid") or 0) > 0]
    if len(usable) < TIMELAPSE_MIN_FRAMES:
        raise ValueError(f"Fewer than {TIMELAPSE_MIN_FRAMES} years have cloud-free Landsat coverage for this AOI in {y0}–{y1}.")
    skipped = [p["year"] for p in rows if p not in usable]

    vis = {"bands": ["red", "green", "blue"], "min": TIMELAPSE_DISPLAY_MIN, "max": TIMELAPSE_DISPLAY_MAX}
    thumb_params = {**vis, "dimensions": TIMELAPSE_THUMB_PX, "region": region, "format": "png"}

    def _thumb(year):
        try:
            return _year_composite(merged, year).getThumbURL(thumb_params)
        except Exception as e:
            logger.warning(f"timelapse: thumbnail for {year} failed: {type(e).__name__}: {e}")
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        urls = list(pool.map(_thumb, [p["year"] for p in usable]))

    frames = [{
        "year": p["year"], "scene_count": int(p["n"]),
        "valid_pixel_fraction": round(p.get("valid") or 0, 4),
        "mean_ndvi": None if p.get("ndvi") is None else round(p["ndvi"], 4),
        "thumb_url": u,
    } for p, u in zip(usable, urls)]

    series = [(f"{f['year']}-07-01", f["mean_ndvi"]) for f in frames if f["mean_ndvi"] is not None]
    trend = (mann_kendall_test([v for _, v in series], [d for d, _ in series]) if len(series) >= trend_stats.MIN_POINTS
             else {"status": "insufficient_data", "note": f"Only {len(series)} yearly NDVI values — Mann-Kendall needs at least {trend_stats.MIN_POINTS}."})

    first_y, last_y = usable[0]["year"], usable[-1]["year"]
    last_img = _year_composite(merged, last_y)
    return {
        "frames": frames, "skipped_years": skipped, "years": [first_y, last_y],
        "ndvi_trend": trend,
        "map_layers": {
            "first": {"year": first_y, "map_layer": _tile_layer(_year_composite(merged, first_y), vis)},
            "last": {"year": last_y, "map_layer": _tile_layer(last_img, vis)},
        },
        "display_stretch": f"{TIMELAPSE_DISPLAY_MIN}–{TIMELAPSE_DISPLAY_MAX} surface reflectance (fixed, display only)",
        "download_url": _download_url(last_img, region, scale=TIMELAPSE_SCALE_M),
        "download_note": f"Raw surface-reflectance bands (blue, green, red, NIR) of the {last_y} composite at 30 m.",
        "caveats": [
            "Each frame is a full-calendar-year median, so seasons and monsoon timing differ between frames; a dry-year frame can look 'browner' without any land change.",
            "Frames mix Landsat 5, 7, 8 and 9 without cross-sensor harmonisation; small reflectance/NDVI steps at sensor changes are possible, and the NDVI trend should be read with that in mind.",
            "Landsat 7 ETM+ imagery has known scan-line gaps in its later years; compositing across scenes mostly fills them but residual striping can remain.",
            "Check each frame's valid-pixel fraction and scene count: a frame built from few scenes or little clear sky is less reliable.",
            "Frame images are generated on demand from Earth Engine and the URLs may expire; reload the tool result if frames stop loading.",
        ],
        "method": (
            "Landsat 5/7/8/9 Collection 2 Level-2 surface reflectance (DN × 0.0000275 − 0.2), masked for fill, dilated cloud, cloud and "
            "cloud shadow with QA_PIXEL (snow kept), median-composited per calendar year at 30 m and shown in natural colour with a fixed "
            "0–0.3 reflectance stretch. Per frame: scene count, share of the AOI with valid pixels, AOI-mean NDVI (nir−red)/(nir+red), "
            "plus a Mann-Kendall / Sen's slope trend on the yearly NDVI series."
        ),
        "aoi_area_km2": round(_region_area_km2(region), 3),
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
            dem = copernicus_dem().clip(region)
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
        if tool == "dynamic_world":
            col = ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1").filterBounds(region).filterDate(params["start_date"], params["end_date"])
            label_mode = col.select("label").mode().clip(region)
            return _fetch_thumb_bytes(label_mode, region, {"min": 0, "max": 8, "palette": DYNAMIC_WORLD_PALETTE})
        if tool == "flood_mapping":
            polarization = params.get("polarization") or "VH"

            def _s1_col(start, end):
                return (
                    ee.ImageCollection("COPERNICUS/S1_GRD").filterBounds(region).filterDate(start, end)
                    .filter(ee.Filter.eq("instrumentMode", "IW"))
                    .filter(ee.Filter.listContains("transmitterReceiverPolarisation", polarization))
                )
            pre_all = _s1_col(params["pre_start"], params["pre_end"])
            orbit = "ASCENDING" if pre_all.filter(ee.Filter.eq("orbitProperties_pass", "ASCENDING")).size().getInfo() \
                >= pre_all.filter(ee.Filter.eq("orbitProperties_pass", "DESCENDING")).size().getInfo() else "DESCENDING"
            pre = pre_all.filter(ee.Filter.eq("orbitProperties_pass", orbit)).select(polarization).mean() \
                .focal_mean(FLOOD_SPECKLE_SMOOTHING_RADIUS_M, "circle", "meters")
            post = _s1_col(params["post_start"], params["post_end"]).filter(ee.Filter.eq("orbitProperties_pass", orbit)) \
                .select(polarization).mean().focal_mean(FLOOD_SPECKLE_SMOOTHING_RADIUS_M, "circle", "meters")
            permanent_water = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence").gte(90)
            flood_mask = post.divide(pre).gt(FLOOD_RATIO_THRESHOLD).And(permanent_water.Not()).selfMask()
            return _fetch_thumb_bytes(flood_mask, region, {"palette": ["#2166ac"]})
        if tool == "soil_moisture":
            start_date, end_date = params["start_date"], params["end_date"]
            end_ee = _cap_end_date(end_date)
            col = (ee.ImageCollection("NASA/SMAP/SPL4SMGP/008").filterBounds(region)
                   .filterDate(ee.Date(start_date), end_ee).select("sm_surface"))
            if col.size().getInfo() == 0:
                return None
            sm = col.mean().clip(region)
            return _fetch_thumb_bytes(sm, region, {"min": 0, "max": 0.5, "palette": ["#a0522d", "#c9a86a", "#e8e2c8", "#8ab4cc", "#1a4d7a"]})
        if tool == "spectral_composites":
            # One picture per report: the first requested composite (natural colour if none were named).
            cid = (params.get("composites") or ["natural_color"])[0]
            spec = SPECTRAL_COMPOSITES.get(cid)
            if spec is None:
                return None
            if spec["sensor"] == "S2":
                composite, _ = _s2_composite(region, params["start_date"], params["end_date"])
                return _fetch_thumb_bytes(composite, region, {"bands": spec["bands"], "min": COMPOSITE_DISPLAY_MIN, "max": COMPOSITE_DISPLAY_MAX})
            s1 = _s1_composite_image(region, params["start_date"], params["end_date"])
            return _fetch_thumb_bytes(s1["image"], region, {
                "bands": spec["bands"],
                "min": [SAR_COMPOSITE_DB_MIN, SAR_COMPOSITE_DB_MIN, SAR_RATIO_DISPLAY_MIN],
                "max": [SAR_COMPOSITE_DB_MAX, SAR_COMPOSITE_DB_MAX, SAR_RATIO_DISPLAY_MAX],
            })
        if tool == "vegetation_drought_index":
            b = _build_vhi_stack(region, params["start_date"], params["end_date"])
            return _fetch_thumb_bytes(b["vhi"], region, {"min": 0, "max": 100, "palette": ["#7f0000", "#d7301f", "#fc8d59", "#fdd49e", "#ffffbf", "#a6d96a", "#1a9850"]})
        if tool == "night_lights":
            col = (ee.ImageCollection(VIIRS_MONTHLY_ID).filterBounds(region)
                   .filterDate(ee.Date(params["start_date"]), _cap_end_date(params["end_date"]).advance(1, "day")).select("avg_rad"))
            if col.size().getInfo() == 0:
                return None
            return _fetch_thumb_bytes(col.mean(), region, {"min": VIIRS_DISPLAY_MIN, "max": VIIRS_DISPLAY_MAX, "palette": NIGHT_LIGHTS_PALETTE})
        if tool == "gedi_forest_structure":
            col = _gedi_collection(GEDI_L2A_MONTHLY, "quality_flag", ["rh98"], region, params["start_date"], params["end_date"])
            if col.size().getInfo() == 0:
                return None
            return _fetch_thumb_bytes(col.mean(), region, {"bands": ["rh98"], "min": GEDI_RH_DISPLAY_MIN, "max": GEDI_RH_DISPLAY_MAX, "palette": GEDI_HEIGHT_PALETTE})
        if tool == "timelapse":
            import datetime as _dt
            y1 = _dt.date.fromisoformat(params["end_date"]).year
            merged = _landsat_merged(region, y1, y1)
            return _fetch_thumb_bytes(_year_composite(merged, y1), region, {"bands": ["red", "green", "blue"], "min": TIMELAPSE_DISPLAY_MIN, "max": TIMELAPSE_DISPLAY_MAX})
        return None  # atmospheric_composition, index_time_series, accuracy_assessment (not single-raster thumbnails)
    except Exception as e:
        logger.warning(f"get_report_thumbnail failed for tool={tool}: {type(e).__name__}: {e}")
        return None
