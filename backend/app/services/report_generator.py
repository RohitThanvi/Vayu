"""
report_generator.py — professional, scientific-format PDF reports for every
analysis type Vayu supports (the 9 satellite metrics + the agri risk score).

Deliberately built with reportlab + deterministic Python text templates
rather than an LLM call for the narrative sections. Every number in the
"Findings & Interpretation" section is pulled directly from the computed
metrics and stitched into fixed sentence templates with explicit thresholds
— this is more consistent and auditable for a document calling itself
scientific than freeform LLM prose would be (same numbers in, same wording
out, every time), and it's cheap/fast/offline. If a future need arises for
genuinely free-form narrative (e.g. summarizing many reports together), an
LLM call could be added as a separate optional section — the structure
here doesn't preclude it, it just isn't needed for a single-analysis report.
"""

import io
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, HRFlowable, KeepTogether
)

LOGO_PATH = os.path.join(os.path.dirname(__file__), "..", "assets", "vayu_logo.png")

INK = colors.HexColor("#12151a")
MUTED = colors.HexColor("#5c6673")
ACCENT = colors.HexColor("#7eb8d4")
GOLD = colors.HexColor("#c9933a")
LINE = colors.HexColor("#c8cdd4")
TABLE_HEAD_BG = colors.HexColor("#eef1f4")


# ═════════════════════════════════════════════════════════════════════════════
# AOI helpers
# ═════════════════════════════════════════════════════════════════════════════

def _extract_coords(geom: Dict[str, Any]) -> List[Tuple[float, float]]:
    """Flatten a Polygon/MultiPolygon (or Feature/FeatureCollection wrapping
    one) into a flat list of (lon, lat) pairs, for bbox/centroid purposes."""
    t = geom.get("type")
    if t == "FeatureCollection":
        pts = []
        for f in geom.get("features", []):
            pts.extend(_extract_coords(f.get("geometry", {})))
        return pts
    if t == "Feature":
        return _extract_coords(geom.get("geometry", {}))
    if t == "Polygon":
        pts = []
        for ring in geom.get("coordinates", []):
            pts.extend([(c[0], c[1]) for c in ring])
        return pts
    if t == "MultiPolygon":
        pts = []
        for poly in geom.get("coordinates", []):
            for ring in poly:
                pts.extend([(c[0], c[1]) for c in ring])
        return pts
    return []


def aoi_summary(geom: Dict[str, Any]) -> Dict[str, Any]:
    coords = _extract_coords(geom)
    if not coords:
        return {"bbox": None, "centroid": None, "vertex_count": 0}
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    bbox = {"min_lon": min(lons), "max_lon": max(lons), "min_lat": min(lats), "max_lat": max(lats)}
    centroid = {"lon": sum(lons) / len(lons), "lat": sum(lats) / len(lats)}
    return {"bbox": bbox, "centroid": centroid, "vertex_count": len(coords), "geometry_type": geom.get("type")}


# ═════════════════════════════════════════════════════════════════════════════
# Per-analysis specifications: sources, methodology, metric labels, findings
# ═════════════════════════════════════════════════════════════════════════════

def _fmt_num(v, decimals=2):
    if v is None:
        return "N/A"
    try:
        return f"{float(v):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(v)


def _vegetation_findings(m: Dict) -> List[str]:
    loss = m.get("vegetation_loss_km2", 0) or 0
    gain = m.get("vegetation_gain_km2", 0) or 0
    initial = m.get("initial_vegetation_km2", 0) or 0
    loss_pct = m.get("loss_pct", 0) or 0
    net = m.get("net_change_km2", 0) or 0
    direction = "a net loss" if net < 0 else "a net gain" if net > 0 else "no net change"
    p1 = (
        f"Over the analysis period, {loss:,.2f} km\u00b2 of land classified as vegetated at the start "
        f"of the period (NDVI \u2265 0.20) no longer met that threshold by the end of the period, while "
        f"{gain:,.2f} km\u00b2 of previously non-vegetated land crossed above the threshold. Against an "
        f"initial vegetated extent of {initial:,.2f} km\u00b2, this represents {direction} of "
        f"{abs(net):,.2f} km\u00b2 ({loss_pct:.2f}% of the initial vegetated area was lost)."
    )
    p2 = (
        "A loss share below 5% is typically within normal seasonal and inter-annual variability for "
        "most land cover types. A loss share above 15% of the initial vegetated extent within a single "
        "analysis window is a stronger signal and generally warrants field verification, particularly "
        "where it coincides with known land-use change, drought conditions, or fire activity in the same "
        "period." if loss_pct >= 5 else
        "This level of change falls within the range typically attributable to normal seasonal and "
        "inter-annual vegetation variability rather than a structural land-cover change, though localized "
        "field verification is still recommended before drawing operational conclusions."
    )
    return [p1, p2]


def _builtup_findings(m: Dict) -> List[str]:
    gain = m.get("built_up_gain_km2", m.get("builtup_gain_km2", 0)) or 0
    loss = m.get("built_up_loss_km2", m.get("builtup_loss_km2", 0)) or 0
    initial = m.get("initial_built_up_km2", m.get("initial_builtup_km2", 0)) or 0
    net = m.get("net_change_km2", 0) or 0
    p1 = (
        f"Dynamic World land-cover classification identifies {gain:,.2f} km\u00b2 of newly built-up land "
        f"and {loss:,.2f} km\u00b2 reverting from built-up to another class over the period, against an "
        f"initial built-up extent of {initial:,.2f} km\u00b2 \u2014 a net change of {net:+,.2f} km\u00b2."
    )
    p2 = (
        "Built-up gain concentrated at the urban fringe is consistent with peri-urban expansion and "
        "densification, a common and generally expected pattern for growing settlements; built-up loss "
        "is comparatively unusual and can indicate demolition, land-use conversion, or a classification "
        "artifact from cloud/shadow contamination in one of the two comparison periods, and is worth "
        "spot-checking against the source imagery where it is material."
    )
    return [p1, p2]


def _water_findings(m: Dict) -> List[str]:
    gain = m.get("water_gain_km2", 0) or 0
    loss = m.get("water_loss_km2", 0) or 0
    initial = m.get("initial_water_km2", 0) or 0
    p1 = (
        f"Surface water extent (JRC Global Surface Water) increased by {gain:,.2f} km\u00b2 and decreased "
        f"by {loss:,.2f} km\u00b2 relative to an initial extent of {initial:,.2f} km\u00b2 over the analysis "
        f"period."
    )
    p2 = (
        "Surface water extent is highly seasonal in most basins; a comparison spanning less than a full "
        "annual cycle should be interpreted as a within-season snapshot rather than a durable trend. "
        "Sustained loss confirmed across multiple analysis windows spanning several years is a more "
        "reliable indicator of a genuine hydrological change than a single-period comparison."
    )
    return [p1, p2]


def _flood_findings(m: Dict) -> List[str]:
    flood_km2 = m.get("flood_area_km2", 0) or 0
    raw_km2 = m.get("raw_flood_area_km2", flood_km2) or 0
    scenes = m.get("scene_count", m.get("scenes_used", "N/A"))
    p1 = (
        f"SAR backscatter change detection (Sentinel-1) against a pre-event reference window identifies "
        f"{flood_km2:,.2f} km\u00b2 of newly inundated area after excluding permanent water bodies (JRC "
        f"Global Surface Water) and steep terrain (SRTM slope > 5\u00b0) that produces backscatter changes "
        f"unrelated to flooding. Before these exclusions and speckle filtering, the raw change signal "
        f"covered {raw_km2:,.2f} km\u00b2, computed from {scenes} usable SAR scenes."
    )
    p2 = (
        "SAR-based flood detection is not affected by cloud cover, unlike optical imagery, which makes it "
        "the appropriate method for active flood events. Remaining sources of false positives include "
        "wind-roughened open water, standing water in agricultural fields (which can resemble flood "
        "signal), and radar shadow in complex terrain; the filtered figure above already accounts for the "
        "most common of these but does not replace ground verification for damage assessment or "
        "emergency response decisions."
    )
    return [p1, p2]


def _fire_findings(m: Dict) -> List[str]:
    count = m.get("fire_count", m.get("hotspot_count", 0)) or 0
    area = m.get("affected_area_km2", 0) or 0
    p1 = (
        f"NASA FIRMS thermal anomaly detections recorded {int(count)} fire/thermal hotspots within the "
        f"area of interest over the analysis period, with an estimated affected footprint of "
        f"{area:,.2f} km\u00b2 based on the detection resolution."
    )
    p2 = (
        "FIRMS detections include both wildfire and non-wildfire thermal sources (agricultural burning, "
        "gas flaring, industrial heat sources); a high hotspot count alone does not confirm wildfire "
        "activity without corroborating context such as land cover, seasonality, and duration/spread "
        "pattern of the detections."
    )
    return [p1, p2]


def _drought_findings(m: Dict) -> List[str]:
    drought_km2 = m.get("drought_affected_km2", 0) or 0
    severe_km2 = m.get("severe_drought_affected_km2", 0) or 0
    nddi_start = m.get("avg_nddi_start", m.get("start_nddi", 0)) or 0
    nddi_end = m.get("avg_nddi_end", m.get("end_nddi", 0)) or 0
    p1 = (
        f"The Normalized Difference Drought Index (NDDI = (NDVI \u2212 NDWI) / (NDVI + NDWI)) identifies "
        f"{drought_km2:,.2f} km\u00b2 under drought stress (NDDI > 0.5) at the end of the analysis period, "
        f"of which {severe_km2:,.2f} km\u00b2 meets the severe threshold (NDDI > 0.7). The area-mean NDDI "
        f"moved from {nddi_start:.3f} at the start of the period to {nddi_end:.3f} at the end."
    )
    p2 = (
        "NDDI is a relative vegetation-moisture index, not a direct soil-moisture or precipitation "
        "measurement; it reflects the combined vegetation and surface-water optical signal and is best "
        "read alongside soil moisture and precipitation records rather than in isolation, particularly "
        "for irrigation and crop-insurance decisions."
    )
    return [p1, p2]


def _lst_findings(m: Dict) -> List[str]:
    start_t = m.get("start_mean_lst_c", 0) or 0
    end_t = m.get("end_mean_lst_c", 0) or 0
    uhi_km2 = m.get("uhi_area_km2", 0) or 0
    delta = end_t - start_t
    p1 = (
        f"Landsat thermal band (ST_B10) derived land surface temperature averaged {start_t:.2f}\u00b0C at "
        f"the start of the period and {end_t:.2f}\u00b0C at the end ({delta:+.2f}\u00b0C). "
        f"{uhi_km2:,.2f} km\u00b2 was classified as an urban heat island pixel (surface temperature more "
        f"than 2\u00b0C above the area mean) in the end-period composite."
    )
    p2 = (
        "Land surface temperature (the temperature of the ground/canopy surface itself) is measured here, "
        "not near-surface air temperature as reported by weather stations \u2014 the two are correlated but "
        "not interchangeable, and LST values are typically higher than air temperature over impervious "
        "surfaces during daytime satellite overpasses."
    )
    return [p1, p2]


def _deforestation_findings(m: Dict) -> List[str]:
    loss_km2 = m.get("forest_loss_km2", 0) or 0
    initial_km2 = m.get("initial_forest_km2", 0) or 0
    loss_pct = (loss_km2 / initial_km2 * 100) if initial_km2 else 0
    p1 = (
        f"{loss_km2:,.2f} km\u00b2 of forest cover present at the start of the period was no longer "
        f"classified as forest by the end of the period, against an initial forested extent of "
        f"{initial_km2:,.2f} km\u00b2 ({loss_pct:.2f}% of initial forest cover)."
    )
    p2 = (
        "This method identifies canopy-cover loss and does not itself distinguish cause (clear-cutting, "
        "fire, selective logging, or natural disturbance such as windthrow or disease). Cross-referencing "
        "against the fire-detection analysis for the same area and period can help rule in or out fire as "
        "a contributing cause."
    )
    return [p1, p2]


def _soil_moisture_findings(m: Dict) -> List[str]:
    start_sm = m.get("start_avg_soil_moisture", 0) or 0
    end_sm = m.get("end_avg_soil_moisture", 0) or 0
    change = m.get("moisture_change", end_sm - start_sm) or 0
    dry_km2 = m.get("dry_stress_area_km2", 0) or 0
    p1 = (
        f"SMAP root-zone soil moisture averaged {start_sm:.4f} m\u00b3/m\u00b3 at the start of the period "
        f"and {end_sm:.4f} m\u00b3/m\u00b3 at the end ({change:+.4f} m\u00b3/m\u00b3). "
        f"{dry_km2:,.2f} km\u00b2 fell below the 0.10 m\u00b3/m\u00b3 dry-stress threshold in the end-period "
        f"composite."
    )
    p2 = (
        "SMAP soil moisture is reported at a coarse ~10 km grid \u2014 suitable for regional/district-scale "
        "moisture trends, not parcel-level irrigation decisions. Values represent an average condition "
        "across each grid cell and can mask meaningful sub-cell variability from mixed land cover or "
        "localized irrigation."
    )
    return [p1, p2]


ANALYSIS_SPECS: Dict[str, Dict[str, Any]] = {
    "vegetation_change": {
        "title": "Vegetation Change Analysis",
        "sources": ["Sentinel-2 SR Harmonized (COPERNICUS/S2_SR_HARMONIZED)", "10 m spatial resolution"],
        "methodology": [
            "Vegetated extent is derived from the Normalized Difference Vegetation Index "
            "(NDVI = (NIR \u2212 Red) / (NIR + Red)), computed from Sentinel-2 bands B8 (NIR) and B4 (Red).",
            "Cloud-masked median composites are built independently for the start and end of the "
            "analysis window, each drawn from a full year centered on the respective date to ensure "
            "adequate cloud-free coverage. Pixels with NDVI \u2265 0.20 are classified as vegetated.",
            "Loss is defined as pixels vegetated in the start composite but not the end composite; gain "
            "is the reverse. Areas are computed via a 30 m-scale pixel-area reduction over the AOI.",
        ],
        "metric_labels": {
            "vegetation_loss_km2": ("Vegetation Loss", "km\u00b2", 4),
            "vegetation_gain_km2": ("Vegetation Gain", "km\u00b2", 4),
            "initial_vegetation_km2": ("Initial Vegetated Area", "km\u00b2", 4),
            "net_change_km2": ("Net Change", "km\u00b2", 4),
            "loss_pct": ("Loss (% of initial)", "%", 4),
        },
        "findings_fn": _vegetation_findings,
        "limitations": (
            "NDVI-based classification is sensitive to atmospheric conditions, seasonal phenology, and "
            "the 0.20 threshold's applicability across different land cover types; a single fixed "
            "threshold may misclassify sparse natural vegetation or dense agricultural land at the "
            "margins."
        ),
    },
    "builtup_change": {
        "title": "Built-Up Area Change Analysis",
        "sources": ["Google Dynamic World V1 (GOOGLE/DYNAMICWORLD/V1)", "10 m spatial resolution"],
        "methodology": [
            "Built-up extent is derived from the Dynamic World near-real-time land cover classifier, "
            "which assigns each pixel to one of nine classes via a deep-learning model trained on "
            "Sentinel-2 imagery.",
            "The modal (most frequent) classification across all available scenes in a one-year window "
            "centered on each comparison date is used as the composite label for that period, reducing "
            "the influence of any single misclassified scene.",
            "Change is computed as the difference between the start- and end-period built-up masks; "
            "areas are computed via a pixel-area reduction over the AOI.",
        ],
        "metric_labels": {
            "built_up_gain_km2": ("Built-Up Gain", "km\u00b2", 4),
            "built_up_loss_km2": ("Built-Up Loss", "km\u00b2", 4),
            "initial_built_up_km2": ("Initial Built-Up Area", "km\u00b2", 4),
            "net_change_km2": ("Net Change", "km\u00b2", 4),
        },
        "findings_fn": _builtup_findings,
        "limitations": (
            "Dynamic World's modal-composite approach can lag genuine rapid construction by several "
            "months and is subject to the classifier's own error rate, which varies by region and land "
            "cover complexity."
        ),
    },
    "water_change": {
        "title": "Surface Water Change Analysis",
        "sources": ["JRC Global Surface Water (JRC/GSW1_4)", "30 m spatial resolution"],
        "methodology": [
            "Surface water extent is derived from the JRC Global Surface Water monthly water history "
            "product, which classifies each Landsat pixel as water or non-water based on spectral "
            "signature across the full multi-decadal Landsat archive.",
            "Start- and end-period water masks are built as an occurrence-frequency composite over each "
            "comparison window; gain/loss areas are the pixel-wise difference between the two masks.",
        ],
        "metric_labels": {
            "water_gain_km2": ("Water Gain", "km\u00b2", 4),
            "water_loss_km2": ("Water Loss", "km\u00b2", 4),
            "initial_water_km2": ("Initial Water Extent", "km\u00b2", 4),
        },
        "findings_fn": _water_findings,
        "limitations": (
            "Surface water extent is strongly seasonal; comparisons across different seasons within the "
            "two composite windows can register as change even absent any durable hydrological shift."
        ),
    },
    "flood_detection": {
        "title": "Flood Detection Analysis (SAR)",
        "sources": ["Sentinel-1 SAR GRD (COPERNICUS/S1_GRD)", "10 m spatial resolution"],
        "methodology": [
            "Flood extent is derived from Synthetic Aperture Radar (SAR) backscatter change detection, "
            "which is unaffected by cloud cover \u2014 standing water produces a characteristic drop in "
            "VH-polarization backscatter relative to a pre-event reference window (30 days prior to the "
            "event window).",
            "A focal median speckle filter is applied to reduce radar speckle noise before change "
            "detection. Permanent water bodies (JRC Global Surface Water) and steep terrain (SRTM slope "
            "> 5\u00b0, where radar layover/shadow produces false backscatter changes) are excluded from "
            "the final flood mask.",
        ],
        "metric_labels": {
            "flood_area_km2": ("Flood Area (filtered)", "km\u00b2", 4),
            "raw_flood_area_km2": ("Flood Area (unfiltered)", "km\u00b2", 4),
            "scene_count": ("SAR Scenes Used", "", 0),
        },
        "findings_fn": _flood_findings,
        "limitations": (
            "SAR flood mapping follows UN-SPIDER standard methodology but remains an approximation; "
            "vegetation canopy can mask flooding beneath it (radar cannot see through dense canopy to "
            "standing water at the surface), and urban flooding is systematically under-detected due to "
            "complex backscatter from building structures."
        ),
    },
    "fire_detection": {
        "title": "Fire / Thermal Anomaly Detection Analysis",
        "sources": ["NASA FIRMS (VIIRS/MODIS active fire detections)", "375 m (VIIRS) / 1 km (MODIS) nominal resolution"],
        "methodology": [
            "Active fire and thermal anomaly detections are sourced from NASA's Fire Information for "
            "Resource Management System (FIRMS), which flags pixels exceeding a temperature threshold "
            "relative to their surroundings in twice-daily satellite overpasses.",
            "Detections are filtered spatially to the AOI and temporally to the analysis window; affected "
            "area is estimated from the nominal pixel footprint of each detection.",
        ],
        "metric_labels": {
            "fire_count": ("Hotspot Detections", "", 0),
            "affected_area_km2": ("Estimated Affected Area", "km\u00b2", 4),
        },
        "findings_fn": _fire_findings,
        "limitations": (
            "FIRMS detects thermal anomalies broadly, not confirmed wildfires specifically; detections "
            "can include agricultural burning, gas flares, and other industrial heat sources, and small "
            "or short-duration fires below the sensor's detection threshold are missed entirely."
        ),
    },
    "drought_index": {
        "title": "Drought Severity Analysis (NDDI)",
        "sources": ["Sentinel-2 SR Harmonized (COPERNICUS/S2_SR_HARMONIZED)", "10 m spatial resolution"],
        "methodology": [
            "Drought stress is assessed via the Normalized Difference Drought Index "
            "(NDDI = (NDVI \u2212 NDWI) / (NDVI + NDWI)), which combines vegetation vigor (NDVI) and "
            "surface water content (NDWI, from Sentinel-2 bands B3/Green and B8/NIR) into a single index "
            "where higher values indicate greater drought stress.",
            "Pixels with NDDI > 0.5 are classified as drought-affected; NDDI > 0.7 as severely "
            "drought-affected. Composites are built from cloud-masked medians over each comparison "
            "window.",
        ],
        "metric_labels": {
            "drought_affected_km2": ("Drought-Affected Area", "km\u00b2", 4),
            "severe_drought_affected_km2": ("Severely Drought-Affected Area", "km\u00b2", 4),
            "avg_nddi_start": ("Mean NDDI (start)", "", 4),
            "avg_nddi_end": ("Mean NDDI (end)", "", 4),
        },
        "findings_fn": _drought_findings,
        "limitations": (
            "NDDI is a vegetation-optical proxy for drought stress, not a direct precipitation or soil "
            "moisture measurement, and its 0.5/0.7 thresholds are general-purpose rather than calibrated "
            "to any specific crop or region."
        ),
    },
    "land_surface_temperature": {
        "title": "Land Surface Temperature / Urban Heat Island Analysis",
        "sources": ["Landsat 8/9 Collection 2 Level-2 (thermal band ST_B10)", "30 m (resampled) spatial resolution"],
        "methodology": [
            "Land surface temperature is derived from the Landsat thermal band (ST_B10) using the "
            "standard USGS Collection 2 scaling: LST(\u00b0C) = ST_B10 \u00d7 0.00341802 + 149.0 \u2212 273.15.",
            "Start- and end-period composites are built as the mean LST across all available scenes in "
            "a one-year window centered on each comparison date. Urban heat island pixels are those "
            "exceeding the AOI's end-period mean temperature by more than 2\u00b0C.",
        ],
        "metric_labels": {
            "start_mean_lst_c": ("Start Mean LST", "\u00b0C", 2),
            "end_mean_lst_c": ("End Mean LST", "\u00b0C", 2),
            "start_min_lst_c": ("Start Min LST", "\u00b0C", 2),
            "start_max_lst_c": ("Start Max LST", "\u00b0C", 2),
            "end_min_lst_c": ("End Min LST", "\u00b0C", 2),
            "end_max_lst_c": ("End Max LST", "\u00b0C", 2),
            "uhi_area_km2": ("Urban Heat Island Area", "km\u00b2", 4),
        },
        "findings_fn": _lst_findings,
        "limitations": (
            "Land surface temperature reflects the radiating surface (ground, roof, canopy), not "
            "near-surface air temperature; comparisons against weather-station air temperature records "
            "should account for this systematic offset."
        ),
    },
    "deforestation": {
        "title": "Deforestation / Forest Cover Loss Analysis",
        "sources": ["Sentinel-2 SR Harmonized (COPERNICUS/S2_SR_HARMONIZED)", "10 m spatial resolution"],
        "methodology": [
            "Forest cover is classified via NDVI thresholding on cloud-masked median composites for the "
            "start and end of the analysis window, following the same compositing approach as the "
            "vegetation-change analysis but with a higher NDVI threshold tuned to closed-canopy forest.",
            "Loss is defined as pixels meeting the forest threshold in the start composite but not the "
            "end composite; area is computed via a pixel-area reduction over the AOI.",
        ],
        "metric_labels": {
            "forest_loss_km2": ("Forest Loss", "km\u00b2", 4),
            "initial_forest_km2": ("Initial Forest Area", "km\u00b2", 4),
        },
        "findings_fn": _deforestation_findings,
        "limitations": (
            "NDVI-threshold forest classification does not distinguish natural forest from dense tree "
            "plantations or orchards, and does not itself identify the cause of any detected loss."
        ),
    },
    "soil_moisture": {
        "title": "Soil Moisture Analysis",
        "sources": ["NASA/USDA SMAP 10km Soil Moisture (NASA_USDA/HSL/SMAP10KM_soil_moisture)", "~10 km spatial resolution"],
        "methodology": [
            "Surface soil moisture (ssm) is sourced from the SMAP 10 km product. Start- and end-period "
            "means are computed as the mean across all available passes in a 3-month window at each end "
            "of the analysis period.",
            "Dry-stress area is the extent falling below 0.10 m\u00b3/m\u00b3 volumetric soil moisture in "
            "the end-period composite.",
        ],
        "metric_labels": {
            "start_avg_soil_moisture": ("Start Mean Soil Moisture", "m\u00b3/m\u00b3", 4),
            "end_avg_soil_moisture": ("End Mean Soil Moisture", "m\u00b3/m\u00b3", 4),
            "moisture_change": ("Change", "m\u00b3/m\u00b3", 4),
            "dry_stress_area_km2": ("Dry-Stress Area", "km\u00b2", 4),
            "end_min_sm": ("End Min Soil Moisture", "m\u00b3/m\u00b3", 4),
            "end_max_sm": ("End Max Soil Moisture", "m\u00b3/m\u00b3", 4),
        },
        "findings_fn": _soil_moisture_findings,
        "limitations": (
            "SMAP's ~10 km native resolution averages over a large area; results are appropriate for "
            "regional/district-scale trends and not for parcel-level irrigation scheduling."
        ),
    },
}


# ═════════════════════════════════════════════════════════════════════════════
# Shared report chrome: header, footer, AOI section
# ═════════════════════════════════════════════════════════════════════════════

def _styles():
    ss = getSampleStyleSheet()
    styles = {
        "brand": ParagraphStyle("brand", parent=ss["Title"], fontSize=22, leading=26,
                                 alignment=TA_CENTER, textColor=INK, spaceAfter=0, fontName="Helvetica-Bold"),
        "brand_sub": ParagraphStyle("brand_sub", parent=ss["Normal"], fontSize=8, leading=10,
                                     alignment=TA_CENTER, textColor=MUTED, tracking=2, fontName="Helvetica"),
        "report_heading": ParagraphStyle("report_heading", parent=ss["Title"], fontSize=15, leading=19,
                                          alignment=TA_CENTER, textColor=INK, spaceBefore=10, spaceAfter=2,
                                          fontName="Helvetica-Bold"),
        "report_sub": ParagraphStyle("report_sub", parent=ss["Normal"], fontSize=9.5, leading=12,
                                      alignment=TA_CENTER, textColor=MUTED, spaceAfter=14),
        "section_head": ParagraphStyle("section_head", parent=ss["Heading2"], fontSize=11.5, leading=14,
                                        textColor=INK, spaceBefore=14, spaceAfter=6, fontName="Helvetica-Bold"),
        "body": ParagraphStyle("body", parent=ss["Normal"], fontSize=9.7, leading=14.5,
                                alignment=TA_JUSTIFY, textColor=INK, spaceAfter=7),
        "meta_label": ParagraphStyle("meta_label", parent=ss["Normal"], fontSize=8.5, textColor=MUTED),
        "meta_value": ParagraphStyle("meta_value", parent=ss["Normal"], fontSize=9.7, textColor=INK, fontName="Helvetica-Bold"),
        "footer": ParagraphStyle("footer", parent=ss["Normal"], fontSize=7.5, textColor=MUTED, alignment=TA_CENTER),
        "caveat": ParagraphStyle("caveat", parent=ss["Normal"], fontSize=8.7, leading=12.5,
                                  alignment=TA_JUSTIFY, textColor=MUTED, spaceAfter=4, fontName="Helvetica-Oblique"),
    }
    return styles


def _footer_canvas(canvas, doc, generated_at: str):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.line(20 * mm, 15 * mm, doc.pagesize[0] - 20 * mm, 15 * mm)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawCentredString(
        doc.pagesize[0] / 2, 10 * mm,
        f"VAYU Geospatial Intelligence \u00b7 Report generated {generated_at} \u00b7 Page {canvas.getPageNumber()}"
    )
    canvas.restoreState()


def _header_flowables(styles, heading: str, subtitle: str) -> List:
    flow = []
    if os.path.exists(LOGO_PATH):
        img = Image(LOGO_PATH, width=13 * mm, height=13 * mm)
        img.hAlign = "CENTER"
        flow.append(img)
        flow.append(Spacer(1, 4))
    flow.append(Paragraph("VAYU", styles["brand"]))
    flow.append(Paragraph("GEOSPATIAL INTELLIGENCE", styles["brand_sub"]))
    flow.append(Spacer(1, 10))
    flow.append(HRFlowable(width="100%", thickness=1, color=LINE))
    flow.append(Paragraph(heading.upper(), styles["report_heading"]))
    flow.append(Paragraph(subtitle, styles["report_sub"]))
    return flow


def _metadata_table(styles, rows: List[Tuple[str, str]]) -> Table:
    data = [[Paragraph(f"<b>{k}</b>", styles["meta_label"]), Paragraph(v, styles["meta_value"])] for k, v in rows]
    t = Table(data, colWidths=[45 * mm, 120 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _metrics_table(styles, rows: List[Tuple[str, str, str]]) -> Table:
    """rows: (label, value, unit)"""
    header = [Paragraph("<b>Metric</b>", styles["meta_label"]), Paragraph("<b>Value</b>", styles["meta_label"]),
               Paragraph("<b>Unit</b>", styles["meta_label"])]
    data = [header] + [[Paragraph(l, styles["body"]), Paragraph(v, styles["meta_value"]), Paragraph(u, styles["body"])]
                        for l, v, u in rows]
    t = Table(data, colWidths=[85 * mm, 45 * mm, 35 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEAD_BG),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, LINE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _study_area_section(styles, aoi_geojson: Dict[str, Any]) -> List:
    summary = aoi_summary(aoi_geojson)
    flow = [Paragraph("1. Study Area", styles["section_head"])]
    bbox = summary.get("bbox")
    centroid = summary.get("centroid")
    rows = [
        ("Geometry Type", summary.get("geometry_type") or "N/A"),
        ("Vertex Count", str(summary.get("vertex_count", 0))),
    ]
    if bbox:
        rows += [
            ("Bounding Box (min lon, min lat)", f"{bbox['min_lon']:.5f}, {bbox['min_lat']:.5f}"),
            ("Bounding Box (max lon, max lat)", f"{bbox['max_lon']:.5f}, {bbox['max_lat']:.5f}"),
        ]
    if centroid:
        rows.append(("Centroid (lon, lat)", f"{centroid['lon']:.5f}, {centroid['lat']:.5f}"))
    flow.append(_metadata_table(styles, rows))
    flow.append(Spacer(1, 4))
    return flow


def _imagery_section(styles, before_bytes: Optional[bytes], after_bytes: Optional[bytes],
                      before_label: str, after_label: str) -> List:
    """Embeds actual satellite imagery (not just derived metrics) side by
    side, so the reader can see the real scene a finding is drawn from.
    Falls back to a single centered image when only one side is available
    (e.g. the agri risk report only has a current-conditions image, not a
    before/after pair)."""
    flow = [Paragraph("2. Satellite Imagery", styles["section_head"])]
    if not before_bytes and not after_bytes:
        flow.append(Paragraph(
            "No cloud-free satellite imagery was available for this AOI within the analysis window to "
            "embed here; the metrics and findings below are unaffected, as they are computed from the "
            "same underlying satellite collections independently of this thumbnail.",
            styles["caveat"]))
        return flow

    if bool(before_bytes) != bool(after_bytes):
        # Single-image case — center it full-width rather than pairing with
        # an empty placeholder column.
        img_bytes = before_bytes or after_bytes
        label = before_label if before_bytes else after_label
        img = Image(io.BytesIO(img_bytes), width=110 * mm, height=110 * mm)
        img.hAlign = "CENTER"
        flow.append(img)
        if label:
            flow.append(Paragraph(f"<i>{label}</i>", ParagraphStyle(
                "img_label_center", parent=styles["footer"], alignment=TA_CENTER)))
        flow.append(Spacer(1, 4))
        return flow

    img_w = 78 * mm
    cells, labels = [], []
    for b, label in ((before_bytes, before_label), (after_bytes, after_label)):
        cells.append(Image(io.BytesIO(b), width=img_w, height=img_w))
        labels.append(Paragraph(f"<i>{label}</i>", styles["footer"]))

    t = Table([cells, labels], colWidths=[img_w + 4, img_w + 4])
    t.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, 0), "TOP"),
        ("TOPPADDING", (0, 1), (-1, 1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    flow.append(t)
    flow.append(Spacer(1, 4))
    return flow


def _methodology_section(styles, sources: List[str], paragraphs: List[str]) -> List:
    flow = [Paragraph("3. Data Sources & Methodology", styles["section_head"])]
    flow.append(Paragraph("<b>Sources:</b> " + "; ".join(sources), styles["body"]))
    for p in paragraphs:
        flow.append(Paragraph(p, styles["body"]))
    return flow


def _results_section(styles, metric_rows: List[Tuple[str, str, str]]) -> List:
    flow = [Paragraph("4. Results", styles["section_head"])]
    flow.append(_metrics_table(styles, metric_rows))
    flow.append(Spacer(1, 4))
    return flow


def _findings_section(styles, paragraphs: List[str]) -> List:
    flow = [Paragraph("5. Findings & Interpretation", styles["section_head"])]
    for p in paragraphs:
        flow.append(Paragraph(p, styles["body"]))
    return flow


def _limitations_section(styles, text: str) -> List:
    flow = [Paragraph("6. Limitations & Caveats", styles["section_head"])]
    flow.append(Paragraph(text, styles["caveat"]))
    flow.append(Paragraph(
        "This report is generated from satellite remote-sensing data and automated processing. It is "
        "intended to support, not replace, field verification and professional judgment for operational, "
        "legal, or financial decisions.",
        styles["caveat"]))
    return flow


# ═════════════════════════════════════════════════════════════════════════════
# Public entry points
# ═════════════════════════════════════════════════════════════════════════════

def build_analysis_report(
    analysis_type: str,
    aoi_geojson: Dict[str, Any],
    start_date: str,
    end_date: str,
    metrics: Dict[str, Any],
    before_image_bytes: Optional[bytes] = None,
    after_image_bytes: Optional[bytes] = None,
) -> bytes:
    """Builds one of the 9 satellite-analysis PDF reports. Returns raw PDF bytes."""
    spec = ANALYSIS_SPECS.get(analysis_type)
    if not spec:
        raise ValueError(f"Unknown analysis_type: {analysis_type}. Must be one of {list(ANALYSIS_SPECS)}")

    styles = _styles()
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm, bottomMargin=20 * mm,
                             leftMargin=20 * mm, rightMargin=20 * mm)

    flow = []
    flow += _header_flowables(styles, spec["title"], f"Analysis Period: {start_date} \u2013 {end_date}")
    flow.append(Spacer(1, 6))
    flow.append(_metadata_table(styles, [
        ("Report Generated", generated_at),
        ("Analysis Type", spec["title"]),
        ("Analysis Period", f"{start_date} to {end_date}"),
    ]))
    flow.append(Spacer(1, 6))

    flow += _study_area_section(styles, aoi_geojson)
    flow += _imagery_section(styles, before_image_bytes, after_image_bytes,
                              f"Start of period ({start_date})", f"End of period ({end_date})")
    flow += _methodology_section(styles, spec["sources"], spec["methodology"])

    metric_rows = []
    for key, (label, unit, decimals) in spec["metric_labels"].items():
        if key in metrics:
            metric_rows.append((label, _fmt_num(metrics[key], decimals), unit))
    flow += _results_section(styles, metric_rows)

    findings = spec["findings_fn"](metrics)
    flow += _findings_section(styles, findings)
    flow += _limitations_section(styles, spec["limitations"])

    doc.build(flow, onFirstPage=lambda c, d: _footer_canvas(c, d, generated_at),
               onLaterPages=lambda c, d: _footer_canvas(c, d, generated_at))
    return buf.getvalue()


def build_agri_risk_report(
    aoi_geojson: Dict[str, Any],
    risk_result: Dict[str, Any],
    region_name: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
) -> bytes:
    """Builds the agri risk-score PDF report from a compute_risk_score() result."""
    styles = _styles()
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm, bottomMargin=20 * mm,
                             leftMargin=20 * mm, rightMargin=20 * mm)

    period = risk_result.get("period", {})
    band = risk_result.get("band", "unknown")
    score = risk_result.get("risk_score")
    confidence = risk_result.get("confidence")

    flow = []
    flow += _header_flowables(
        styles, "Agricultural Risk Assessment Report",
        f"Analysis Period: {period.get('start_date', 'N/A')} \u2013 {period.get('end_date', 'N/A')}"
    )
    flow.append(Spacer(1, 6))
    flow.append(_metadata_table(styles, [
        ("Report Generated", generated_at),
        ("Region", region_name or "Unnamed AOI"),
        ("Analysis Period", f"{period.get('start_date', 'N/A')} to {period.get('end_date', 'N/A')}"),
        ("Composite Risk Score", f"{score} / 100  ({str(band).upper()})"),
        ("Confidence", f"{confidence}%"),
    ]))
    flow.append(Spacer(1, 6))

    flow += _study_area_section(styles, aoi_geojson)
    flow += _imagery_section(styles, None, image_bytes,
                              "", f"Current conditions (as of {period.get('end_date', 'N/A')})")

    flow.append(Paragraph("3. Methodology", styles["section_head"]))
    flow.append(Paragraph(
        "The composite risk score combines three independently-computed satellite indicators \u2014 "
        "drought stress (Sentinel-2 NDDI), vegetation loss (Sentinel-2 NDVI threshold change), and "
        "soil moisture deficit (SMAP) \u2014 into a single 0\u2013100 score. Each indicator is scored 0\u2013100 "
        "and combined via a weighted average (drought 40%, vegetation loss 35%, moisture deficit 25%); "
        "weights are automatically renormalized over whichever indicators successfully computed for this "
        "AOI and period. Confidence reflects both data completeness (how many of the three indicators "
        "were available) and, where the region has accumulated farmer/officer feedback on past alerts, "
        "the region's historical alert accuracy.",
        styles["body"]))

    flow.append(Paragraph("4. Sub-Score Breakdown", styles["section_head"]))
    sub_scores = risk_result.get("sub_scores", {})
    sub_rows = [
        (k.replace("_", " ").title(), _fmt_num(v, 1) if v is not None else "Not computed", "/ 100")
        for k, v in sub_scores.items()
    ]
    flow.append(_metrics_table(styles, sub_rows))
    flow.append(Spacer(1, 4))

    inputs_used = risk_result.get("inputs_used", [])
    inputs_failed = risk_result.get("inputs_failed", [])
    if inputs_failed:
        flow.append(Paragraph(
            f"<b>Note:</b> the following indicators could not be computed for this AOI/period and were "
            f"excluded from the composite score (weights renormalized over the remainder): "
            f"{', '.join(inputs_failed)}.",
            styles["caveat"]))

    flow.append(Paragraph("5. Findings & Interpretation", styles["section_head"]))
    flow.append(Paragraph(risk_result.get("reason", "No specific driver identified."), styles["body"]))
    flow.append(Paragraph(
        f"This assessment carries a confidence of {confidence}%, reflecting "
        f"{'full' if len(inputs_used) == 3 else 'partial'} data availability across the three underlying "
        f"indicators" + (
            " and this region's accumulated alert-accuracy track record." if len(inputs_used) == 3 else "."
        ),
        styles["body"]))

    flow.append(Paragraph("6. Limitations & Caveats", styles["section_head"]))
    flow.append(Paragraph(
        "This is a satellite-derived composite indicator, not a substitute for field inspection, "
        "agronomic assessment, or official crop-loss/insurance determinations. The scoring model is "
        "crop-agnostic (it does not currently account for crop-specific growth stage or water "
        "requirements) and should be interpreted as a general land-condition signal for the AOI as a "
        "whole, not a per-parcel or per-crop diagnosis.",
        styles["caveat"]))
    flow.append(Paragraph(
        "This report is generated from satellite remote-sensing data and automated processing. It is "
        "intended to support, not replace, field verification and professional judgment for operational, "
        "legal, or financial decisions.",
        styles["caveat"]))

    doc.build(flow, onFirstPage=lambda c, d: _footer_canvas(c, d, generated_at),
               onLaterPages=lambda c, d: _footer_canvas(c, d, generated_at))
    return buf.getvalue()
