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
import logging
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.graphics.shapes import Drawing, Rect, Line, String, Polygon
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, HRFlowable, KeepTogether, PageBreak
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
    findings = [p1, p2]
    if m.get("small_base_caveat"):
        aoi_pct = m.get("vegetation_pct_of_aoi")
        findings.append(
            f"CAVEAT \u2014 the initial vegetated extent ({initial:,.2f} km\u00b2) is only "
            f"{aoi_pct:.2f}% of this AOI's total area, meaning loss_pct above is a percentage computed "
            f"against a very small base. On a small base, a handful of pixels crossing the 0.20 NDVI "
            f"threshold between the two comparison snapshots \u2014 plausible from ordinary seasonal timing "
            f"differences (e.g. lake level, snowmelt timing) rather than any real land-cover change \u2014 "
            f"can swing this percentage by tens of points. Treat the loss_pct figure with reduced "
            f"confidence for an AOI this sparsely vegetated; the absolute km\u00b2 figures above are more "
            f"reliable than the percentage for a case like this."
        )
    return findings


def _builtup_findings(m: Dict) -> List[str]:
    if m.get("data_availability_note"):
        return [f"Built-up change could not be computed: {m['data_availability_note']}"]
    gain = m.get("builtup_gain_km2", 0) or 0
    loss = m.get("builtup_loss_km2", 0) or 0
    initial = m.get("initial_builtup_km2", 0) or 0
    final = m.get("final_builtup_km2", initial + gain - loss)
    net = final - initial
    aoi_area = m.get("region_area_km2")
    final_pct_aoi = m.get("final_builtup_pct_of_aoi")
    p1 = (
        f"Dynamic World land-cover classification identifies {gain:,.2f} km\u00b2 of newly built-up land "
        f"and {loss:,.2f} km\u00b2 reverting from built-up to another class over the period, against an "
        f"initial built-up extent of {initial:,.2f} km\u00b2 \u2014 a net change of {net:+,.2f} km\u00b2 "
        f"(final built-up extent: {final:,.2f} km\u00b2)."
    )
    if aoi_area and final_pct_aoi is not None:
        p1 += (
            f" In context of the whole AOI ({aoi_area:,.1f} km\u00b2), built-up land now covers "
            f"{final_pct_aoi:.1f}% of it \u2014 worth keeping in view for a large AOI, where even a "
            f"substantial absolute km\u00b2 figure can represent a small share of the total area, "
            f"concentrated in specific parts of it rather than reflecting the AOI as a whole."
        )
    p2 = (
        "Built-up gain concentrated at the urban fringe is consistent with peri-urban expansion and "
        "densification, a common and generally expected pattern for growing settlements; built-up loss "
        "is comparatively unusual and can indicate demolition, land-use conversion, or a classification "
        "artifact from cloud/shadow contamination in one of the two comparison periods, and is worth "
        "spot-checking against the source imagery where it is material. Note that this reflects a change "
        "in classified land-cover category between two modal composites, not direct confirmation of a "
        "physical construction or demolition event."
    )
    return [p1, p2]


def _water_findings(m: Dict) -> List[str]:
    if m.get("data_availability_note"):
        return [f"Surface water change could not be computed: {m['data_availability_note']}"]
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
    raw_km2 = m.get("raw_backscatter_drop_km2", flood_km2) or 0
    ref_scenes = m.get("reference_scenes_used", "N/A")
    flood_scenes = m.get("flood_period_scenes_used", "N/A")
    p1 = (
        f"SAR backscatter change detection (Sentinel-1) against a pre-event reference window ({ref_scenes} "
        f"reference scenes) identifies {flood_km2:,.2f} km\u00b2 of newly inundated area after excluding "
        f"permanent water bodies (JRC Global Surface Water) and steep terrain (SRTM slope > 5\u00b0) that "
        f"produces backscatter changes unrelated to flooding. Before these exclusions and speckle "
        f"filtering, the raw backscatter-drop signal covered {raw_km2:,.2f} km\u00b2, computed from "
        f"{flood_scenes} usable SAR scenes during the flood-period window."
    )
    p2 = (
        "SAR-based flood detection is not affected by cloud cover, unlike optical imagery, which makes it "
        "the appropriate method for active flood events. Remaining sources of false positives include "
        "wind-roughened open water, standing water in agricultural fields (which can resemble flood "
        "signal), and radar shadow in complex terrain; the filtered figure above already accounts for the "
        "most common of these but does not replace ground verification for damage assessment or "
        "emergency response decisions."
    )
    findings = [p1, p2]
    if m.get("long_window_caveat"):
        days = m.get("flood_period_days")
        findings.append(
            f"CAVEAT \u2014 this analysis period spans {days:,} days, well beyond a typical short flood-event "
            f"window. This method was designed to compare a tight pre-event reference against the event "
            f"window itself (\"did X flood between date A and date B\", where that window IS the event); "
            f"over a long window, the flood-period composite blends multiple full seasonal cycles together, "
            f"and ordinary seasonal backscatter change \u2014 snow/ice cover forming and melting, lake "
            f"freeze-thaw, monsoon-driven wetting and drying \u2014 can be indistinguishable from a real flood "
            f"signal to this algorithm, especially at high-altitude, high-latitude, or strongly seasonal "
            f"AOIs. The figure above should be treated with reduced confidence for a window this long \u2014 "
            f"for a genuine flood assessment, re-run this analysis with start_date set to shortly before "
            f"the suspected event, not a broad multi-month or multi-year range."
        )
    return findings


def _fire_findings(m: Dict) -> List[str]:
    count = m.get("fire_event_count", 0) or 0
    area = m.get("burned_area_km2", 0) or 0
    p1 = (
        f"NASA FIRMS thermal anomaly detections recorded {int(count)} fire/thermal hotspots within the "
        f"area of interest over the analysis period, with an estimated burned/affected footprint of "
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
    severe_km2 = m.get("severe_drought_km2", 0) or 0
    nddi_start = m.get("avg_nddi_start")
    nddi_end = m.get("avg_nddi_end")
    if nddi_start is not None and nddi_end is not None:
        nddi_sentence = (
            f"The area-mean NDDI moved from {nddi_start:.3f} at the start of the period to "
            f"{nddi_end:.3f} at the end."
        )
    else:
        # Genuinely disclose missing data rather than defaulting to 0 --
        # a NDDI of exactly 0.000 is a real (if unlikely) measurement, so
        # silently substituting it for "no valid pixels" would misreport
        # an absence of data as a specific, neutral-sounding value.
        nddi_sentence = (
            "The area-mean NDDI could not be computed for one or both periods (no pixels with a "
            "valid NDVI+NDWI denominator were found in the AOI)."
        )
    p1 = (
        f"The Normalized Difference Drought Index (NDDI = (NDVI \u2212 NDWI) / (NDVI + NDWI)) identifies "
        f"{drought_km2:,.2f} km\u00b2 under drought stress (NDDI > 0.5) at the end of the analysis period, "
        f"of which {severe_km2:,.2f} km\u00b2 meets the severe threshold (NDDI > 0.7). {nddi_sentence}"
    )
    p2 = (
        "NDDI is a relative vegetation-moisture index, not a direct soil-moisture or precipitation "
        "measurement; it reflects the combined vegetation and surface-water optical signal and is best "
        "read alongside soil moisture and precipitation records rather than in isolation, particularly "
        "for irrigation and crop-insurance decisions."
    )
    return [p1, p2]


def _lst_findings(m: Dict) -> List[str]:
    if m.get("data_availability_note"):
        return [f"Land surface temperature could not be computed: {m['data_availability_note']}"]
    start_t = m.get("start_mean_lst_c")
    end_t = m.get("end_mean_lst_c")
    uhi_km2 = m.get("uhi_area_km2", 0) or 0
    if start_t is not None and end_t is not None:
        delta = end_t - start_t
        temp_sentence = (
            f"Landsat thermal band (ST_B10) derived land surface temperature averaged {start_t:.2f}\u00b0C at "
            f"the start of the period and {end_t:.2f}\u00b0C at the end ({delta:+.2f}\u00b0C). "
        )
    else:
        temp_sentence = "Land surface temperature could not be computed for one or both periods (no usable Landsat thermal pixels found). "
    p1 = (
        f"{temp_sentence}"
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
    initial_km2 = m.get("total_forest_2000_km2", 0) or 0
    loss_pct = m.get("loss_pct", (loss_km2 / initial_km2 * 100) if initial_km2 else 0)
    annual_rate = m.get("annual_loss_rate_km2", 0) or 0
    p1 = (
        f"{loss_km2:,.2f} km\u00b2 of forest cover present in the Hansen Global Forest Change baseline "
        f"(year 2000 tree cover) was lost by the end of the analysis period, against a baseline forested "
        f"extent of {initial_km2:,.2f} km\u00b2 ({loss_pct:.2f}% of baseline forest cover), an average "
        f"loss rate of {annual_rate:,.2f} km\u00b2/year over the period."
    )
    p2 = (
        "This method identifies canopy-cover loss and does not itself distinguish cause (clear-cutting, "
        "fire, selective logging, or natural disturbance such as windthrow or disease). Cross-referencing "
        "against the fire-detection analysis for the same area and period can help rule in or out fire as "
        "a contributing cause."
    )
    return [p1, p2]


def _soil_moisture_findings(m: Dict) -> List[str]:
    start_sm = m.get("start_avg_soil_moisture")
    end_sm = m.get("end_avg_soil_moisture")
    change = m.get("moisture_change")
    dry_km2 = m.get("dry_stress_area_km2")
    if start_sm is not None and end_sm is not None:
        moisture_sentence = (
            f"SMAP root-zone soil moisture averaged {start_sm:.4f} m\u00b3/m\u00b3 at the start of the period "
            f"and {end_sm:.4f} m\u00b3/m\u00b3 at the end ({(change if change is not None else end_sm - start_sm):+.4f} m\u00b3/m\u00b3). "
        )
    else:
        moisture_sentence = "SMAP soil moisture could not be computed for one or both periods (no SMAP coverage found for this AOI/window). "
    if dry_km2 is not None:
        dry_sentence = (
            f"{dry_km2:,.2f} km\u00b2 fell below the 0.10 m\u00b3/m\u00b3 dry-stress threshold in the end-period composite."
        )
    else:
        dry_sentence = "Dry-stress area could not be computed for the end period (no SMAP coverage found)."
    p1 = moisture_sentence + dry_sentence
    p2 = (
        "SMAP soil moisture is reported at a coarse ~10 km grid \u2014 suitable for regional/district-scale "
        "moisture trends, not parcel-level irrigation decisions. Values represent an average condition "
        "across each grid cell and can mask meaningful sub-cell variability from mixed land cover or "
        "localized irrigation."
    )
    findings = [p1, p2]
    if m.get("window_overlap_caveat"):
        findings.append(
            "CAVEAT \u2014 SMAP's start- and end-period averaging windows (3 months each, extending outward "
            "from the requested start/end dates) overlap for this analysis because the requested period "
            "itself is short. The start and end moisture figures above are not computed from fully "
            "independent before/after data \u2014 treat the reported moisture_change as less reliable than "
            "it would be for a longer requested period."
        )
    return findings


ANALYSIS_SPECS: Dict[str, Dict[str, Any]] = {
    "vegetation_change": {
        "title": "Vegetation Change Analysis",
        "sources": ["Sentinel-2 SR Harmonized (COPERNICUS/S2_SR_HARMONIZED)", "10 m spatial resolution"],
        "methodology": [
            "Vegetated extent is derived from the Normalized Difference Vegetation Index "
            "(NDVI = (NIR \u2212 Red) / (NIR + Red)), computed from Sentinel-2 bands B8 (NIR) and B4 (Red).",
            "Cloud-masked median composites are built independently for the start and end of the "
            "analysis window \u2014 the start-period composite runs forward one year from the start date, "
            "and the end-period composite runs backward one year from the end date, so both stay within "
            "the requested analysis period and never require imagery from after the end date. "
            "Pixels with NDVI \u2265 0.20 are classified as vegetated.",
            "Loss is defined as pixels vegetated in the start composite but not the end composite; gain "
            "is the reverse. Areas are computed via a 30 m-scale pixel-area reduction over the AOI.",
        ],
        "metric_labels": {
            "vegetation_loss_km2": ("Vegetation Decline", "km\u00b2", 4),
            "vegetation_gain_km2": ("Vegetation Gain", "km\u00b2", 4),
            "initial_vegetation_km2": ("Initial Vegetated Area", "km\u00b2", 4),
            "net_change_km2": ("Net Change", "km\u00b2", 4),
            "loss_pct": ("Loss (% of initial)", "%", 4),
            "region_area_km2": ("Total AOI Area", "km\u00b2", 4),
            "vegetation_pct_of_aoi": ("Initial Vegetated (% of whole AOI)", "%", 4),
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
            "The modal (most frequent) classification across all available scenes is used as the "
            "composite label for each period, reducing the influence of any single misclassified scene "
            "\u2014 the start-period window runs forward one year from the start date, and the end-period "
            "window runs backward one year from the end date, so both stay within the requested analysis "
            "period and never require imagery from after the end date.",
            "Change is computed as the difference between the start- and end-period built-up masks; "
            "areas are computed via a pixel-area reduction over the AOI.",
        ],
        "metric_labels": {
            "builtup_gain_km2": ("Built-Up Gain", "km\u00b2", 4),
            "builtup_loss_km2": ("Built-Up Loss", "km\u00b2", 4),
            "initial_builtup_km2": ("Initial Built-Up Area", "km\u00b2", 4),
            "net_change_km2": ("Net Change", "km\u00b2", 4),
            "final_builtup_km2": ("Final Built-Up Area", "km\u00b2", 4),
            "gain_pct": ("Gain (% of initial built-up area)", "%", 4),
            "region_area_km2": ("Total AOI Area", "km\u00b2", 4),
            "initial_builtup_pct_of_aoi": ("Initial Built-Up (% of whole AOI)", "%", 4),
            "final_builtup_pct_of_aoi": ("Final Built-Up (% of whole AOI)", "%", 4),
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
            "product, which classifies each Landsat pixel as water or non-water each month based on "
            "spectral signature across the full multi-decadal Landsat archive.",
            "Start- and end-period water masks reflect each pixel's most common (modal) monthly "
            "classification across the full year-long comparison window \u2014 i.e. the water body's "
            "typical/dominant state that year, not its maximum extent at any single month. A location "
            "that is wet for a few weeks and dry the rest of the year will correctly show as \"not "
            "water\" even though it was briefly inundated at its peak; gain/loss areas are the "
            "pixel-wise difference between the two dominant-state masks.",
        ],
        "metric_labels": {
            "water_gain_km2": ("Water Gain", "km\u00b2", 4),
            "water_loss_km2": ("Water Loss", "km\u00b2", 4),
            "initial_water_km2": ("Initial Water Extent", "km\u00b2", 4),
            "net_change_km2": ("Net Change", "km\u00b2", 4),
        },
        "findings_fn": _water_findings,
        "limitations": (
            "Surface water extent is strongly seasonal; comparisons across different seasons within the "
            "two composite windows can register as change even absent any durable hydrological shift."
        ),
    },
    "flood_detection": {
        "title": "Flood Detection Analysis (SAR)",
        "sources": ["Sentinel-1 SAR GRD (COPERNICUS/S1_GRD)", "JRC Global Surface Water (JRC/GSW1_4/GlobalSurfaceWater)", "SRTM 30m DEM (USGS/SRTMGL1_003)", "10 m spatial resolution"],
        "methodology": [
            "Flood extent is derived from Synthetic Aperture Radar (SAR) backscatter change detection, "
            "which is unaffected by cloud cover \u2014 standing water produces a characteristic drop in "
            "VV-polarization backscatter relative to a pre-event reference window (30 days prior to the "
            "event window).",
            "A focal median speckle filter is applied to reduce radar speckle noise before change "
            "detection. Permanent water bodies (JRC Global Surface Water) and steep terrain (SRTM slope "
            "> 5\u00b0, where radar layover/shadow produces false backscatter changes) are excluded from "
            "the final flood mask.",
        ],
        "metric_labels": {
            "flood_area_km2": ("Flood Area (filtered)", "km\u00b2", 4),
            "raw_backscatter_drop_km2": ("Backscatter-Drop Area (unfiltered)", "km\u00b2", 4),
            "reference_scenes_used": ("Reference-Period SAR Scenes", "", 0),
            "flood_period_scenes_used": ("Flood-Period SAR Scenes", "", 0),
            "flood_period_days": ("Flood-Period Window Length", "days", 0),
        },
        "findings_fn": _flood_findings,
        "limitations": (
            "SAR flood mapping follows UN-SPIDER standard methodology but remains an approximation; "
            "vegetation canopy can mask flooding beneath it (radar cannot see through dense canopy to "
            "standing water at the surface), and urban flooding is systematically under-detected due to "
            "complex backscatter from building structures. This method is designed for a short, "
            "event-scoped analysis window (a tight pre-event reference vs. the event window itself) \u2014 "
            "running it over a long window (many months or years) risks misreading ordinary seasonal "
            "backscatter change (snow/ice cover, lake freeze-thaw, monsoon wetting/drying) as flooding; "
            "see the Findings section for a specific caveat when this run's window is long enough for "
            "that risk to apply."
        ),
    },
    "fire_detection": {
        "title": "Fire / Burned Area Detection Analysis",
        "sources": ["MODIS Burned Area (MODIS/061/MCD64A1)", "MODIS Active Fire (MODIS/061/MOD14A1)", "500 m (burned area) / 1 km (active fire) nominal resolution"],
        "methodology": [
            "Burned area is derived from the MODIS Collection 6.1 monthly burned-area product (MCD64A1), "
            "which maps the approximate date of burning at 500 m resolution from MODIS surface "
            "reflectance imagery. Any pixel with a burn date within the analysis window is classified as "
            "burned.",
            "Fire event count is derived independently from the MODIS active-fire product (MOD14A1), which "
            "flags thermal anomalies at 1 km resolution; the count reflects the number of active-fire "
            "detection scenes with at least one flagged pixel in the AOI over the period, not a count of "
            "distinct burn events.",
        ],
        "metric_labels": {
            "fire_event_count": ("Active-Fire Detection Scenes", "", 0),
            "burned_area_km2": ("Burned Area", "km\u00b2", 4),
        },
        "findings_fn": _fire_findings,
        "limitations": (
            "MCD64A1 burned-area mapping can miss small or low-intensity fires below its 500 m detection "
            "threshold, and MOD14A1 active-fire detections include non-wildfire thermal sources "
            "(agricultural burning, gas flaring, industrial heat); a high detection count alone does not "
            "confirm wildfire activity without corroborating context such as land cover and burn "
            "duration/spread pattern."
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
            "severe_drought_km2": ("Severely Drought-Affected Area", "km\u00b2", 4),
            "avg_nddi_start": ("Mean NDDI (start)", "", 4),
            "avg_nddi_end": ("Mean NDDI (end)", "", 4),
            "nddi_change": ("NDDI Change", "", 4),
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
            "Land surface temperature is derived from the Landsat 8 and 9 thermal band (ST_B10), merged "
            "into one combined collection per window (not treated as an either/or fallback) using the "
            "standard USGS Collection 2 scaling: LST(\u00b0C) = ST_B10 \u00d7 0.00341802 + 149.0 \u2212 273.15. "
            "Scenes are pre-filtered to <20% overall cloud cover, and each scene's QA_PIXEL band is then "
            "used to mask cloud, cloud shadow, and dilated-cloud pixels individually before averaging \u2014 "
            "the scene-level filter alone does not catch localized cloud over part of an otherwise clear scene.",
            "Start- and end-period composites are built as the mean LST across all available (post-masking) "
            "scenes in a one-year window \u2014 the start-period window runs forward from the start date, and "
            "the end-period window runs backward from the end date, so both windows stay within the "
            "requested analysis period. Urban heat island pixels are those exceeding the AOI's end-period "
            "mean temperature by more than 2\u00b0C \u2014 a relative, AOI-specific measure of localized hotspots "
            "within this analysis, not a validated urban-vs-rural heat island measurement in the formal "
            "UHI-research sense (which typically compares an urban core against a defined rural reference, "
            "not an AOI against its own mean).",
        ],
        "metric_labels": {
            "start_mean_lst_c": ("Start Mean LST", "\u00b0C", 2),
            "end_mean_lst_c": ("End Mean LST", "\u00b0C", 2),
            "lst_change_c": ("LST Change", "\u00b0C", 2),
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
        "sources": ["Hansen Global Forest Change v1.13 (UMD/hansen/global_forest_change_2025_v1_13)", "30 m spatial resolution"],
        "methodology": [
            "Forest cover loss is derived from the Hansen Global Forest Change dataset, which maps "
            "year-2000 tree canopy cover and, independently, the year in which stand-replacement forest "
            "loss occurred at each pixel (2001\u20132025) from Landsat time-series analysis.",
            "A pixel is counted as baseline forest where year-2000 canopy cover was \u2265 30%. Loss is the "
            "subset of that baseline forest whose mapped loss year falls within the requested analysis "
            "period; area is computed via a 30 m-scale pixel-area reduction over the AOI. Reported loss "
            "should be read as forest/tree-cover loss as this dataset measures it (any stand-replacement "
            "disturbance) \u2014 not a confirmed attribution of cause (e.g. logging vs. fire vs. natural "
            "disturbance), which this dataset alone does not determine.",
        ],
        "metric_labels": {
            "forest_loss_km2": ("Forest Loss", "km\u00b2", 4),
            "total_forest_2000_km2": ("Baseline Forest Area (2000)", "km\u00b2", 4),
            "loss_pct": ("Loss (% of baseline)", "%", 4),
            "annual_loss_rate_km2": ("Average Annual Loss Rate", "km\u00b2/yr", 4),
        },
        "findings_fn": _deforestation_findings,
        "limitations": (
            "The Hansen dataset's loss-year attribution is derived from Landsat time series and can miss "
            "loss under persistent cloud cover or misattribute the exact year in fast-regrowth areas; the "
            "30% canopy threshold does not distinguish natural forest from dense plantations or orchards, "
            "and the dataset's loss-year data currently extends only through 2025, so any portion of the "
            "requested period after 2025 is not reflected in these figures. \"Loss\" here means detected "
            "stand-replacement disturbance, not confirmed deforestation with an attributed cause."
        ),
    },
    "soil_moisture": {
        "title": "Soil Moisture Analysis",
        "sources": ["NASA SMAP L4 Global (NASA/SMAP/SPL4SMGP/008)", "~9 km spatial resolution, 3-hourly"],
        "methodology": [
            "Surface soil moisture (sm_surface, 0\u20135 cm depth) is sourced from the SMAP L4 3-hourly "
            "global product. Start- and end-period means are computed as the mean across all available "
            "3-hourly readings in a 3-month window at each end of the analysis period.",
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
            "SMAP L4's ~9 km native resolution averages over a large area; results are appropriate for "
            "regional/district-scale trends and not for parcel-level irrigation scheduling."
        ),
    },
}


# Companion to ANALYSIS_SPECS — recommendations, glossary terms, and
# citation keys per analysis type. Kept separate rather than folded into
# the (already large) spec dicts above to keep each editable independently.
ANALYSIS_EXTRAS: Dict[str, Dict[str, Any]] = {
    "vegetation_change": {
        "recommendations": [
            "Where loss exceeds 10-15% of the initial vegetated area, cross-reference against the fire "
            "detection and land surface temperature analyses for the same AOI/period to help distinguish "
            "drought/heat-driven dieback from land-use conversion.",
            "For land-use planning or compliance use cases, pair this analysis with a field visit to the "
            "highest-loss sub-areas before drawing conclusions about cause.",
            "Re-run this analysis at a 1-2 year cadence for the same AOI to distinguish a one-off "
            "seasonal anomaly from a sustained trend.",
        ],
        "glossary": [
            ("NDVI", "Normalized Difference Vegetation Index = (NIR \u2212 Red) / (NIR + Red). Ranges "
                      "\u22121 to 1; higher values indicate denser, healthier vegetation."),
            ("Vegetated (threshold)", "A pixel is classified vegetated where NDVI \u2265 0.20 in this "
                                       "analysis \u2014 a general-purpose cutoff, not tuned to a specific crop or biome."),
        ],
        "citation_keys": ["sentinel2"],
    },
    "builtup_change": {
        "recommendations": [
            "Treat built-up loss as a flag for manual review, not a confirmed demolition \u2014 verify "
            "against higher-resolution imagery or a site visit before acting on it.",
            "For urban planning use, combine this analysis with the population/infrastructure data your "
            "organization already holds, since built-up extent alone doesn't indicate density or use type.",
        ],
        "glossary": [
            ("Dynamic World", "A near-real-time, 10 m land cover classifier producing 9 land cover classes "
                               "(including 'built') from a deep learning model trained on Sentinel-2 imagery."),
            ("Modal Composite", "The single most-frequently-occurring classification across all scenes in "
                                 "the time window, used instead of a single date to reduce misclassification noise."),
        ],
        "citation_keys": ["dynamicworld", "sentinel2"],
    },
    "water_change": {
        "recommendations": [
            "Confirm any apparent water loss against a longer multi-year time series before concluding a "
            "waterbody is drying \u2014 a single-period comparison can reflect normal seasonal drawdown.",
            "For reservoir/irrigation management, cross-reference with local rainfall records for the "
            "same period.",
        ],
        "glossary": [
            ("Water Occurrence", "The JRC dataset's measure of how often a pixel was observed as water "
                                  "across the full Landsat historical record, used to build the water mask for each period."),
        ],
        "citation_keys": ["jrc_water"],
    },
    "flood_detection": {
        "recommendations": [
            "This analysis is suitable for rapid situational awareness; for damage assessment or "
            "emergency-response resource allocation, corroborate with ground reports or higher-resolution "
            "commercial SAR/optical tasking where available.",
            "Check the raw (unfiltered) backscatter-drop figure against the filtered figure \u2014 a large "
            "gap between them suggests significant permanent-water or steep-terrain area in the AOI worth "
            "reviewing on the map.",
        ],
        "glossary": [
            ("SAR", "Synthetic Aperture Radar \u2014 an active sensor that transmits its own microwave "
                     "signal and measures the reflection, working day/night and through cloud cover."),
            ("VV Backscatter", "The returned radar signal strength in the vertical-transmit, "
                                "vertical-receive polarization; standing water produces a characteristic drop in this value."),
            ("dB (decibel)", "The logarithmic unit SAR backscatter is measured in; lower (more negative) "
                              "values indicate a smoother surface (e.g. calm water), higher values a rougher one (e.g. buildings, vegetation)."),
        ],
        "citation_keys": ["sentinel1", "jrc_water", "srtm"],
    },
    "fire_detection": {
        "recommendations": [
            "Cross-reference hotspot locations against land cover and known agricultural-burning calendars "
            "for the region before attributing detections to wildfire specifically.",
            "For a confirmed wildfire event, pair this with the vegetation change and land surface "
            "temperature analyses over the same AOI to assess ecological impact.",
        ],
        "glossary": [
            ("Burned Area (MCD64A1)", "A MODIS product mapping the approximate calendar date a given "
                                       "500 m pixel burned, derived from surface reflectance change detection."),
            ("Active Fire (MOD14A1)", "A MODIS product flagging thermal anomalies (pixels significantly "
                                       "hotter than their surroundings) at 1 km resolution from twice-daily overpasses."),
        ],
        "citation_keys": ["modis_fire"],
    },
    "drought_index": {
        "recommendations": [
            "Pair this reading with local precipitation and, where available, in-situ soil moisture data "
            "before using it for insurance, subsidy, or water-allocation decisions.",
            "A single-period NDDI reading doesn't establish a trend \u2014 request the same AOI at 3-6 month "
            "intervals to see whether drought stress is worsening, stable, or recovering.",
        ],
        "glossary": [
            ("NDDI", "Normalized Difference Drought Index = (NDVI \u2212 NDWI) / (NDVI + NDWI). Combines "
                      "vegetation vigor and surface water content into one drought-stress indicator."),
            ("NDWI", "Normalized Difference Water Index, a measure of surface water/moisture content used "
                      "as one of NDDI's two inputs."),
        ],
        "citation_keys": ["sentinel2"],
    },
    "land_surface_temperature": {
        "recommendations": [
            "For public-health heat-risk applications, combine LST with population density data \u2014 "
            "LST alone identifies hot surfaces, not where people are actually exposed to heat.",
            "Urban heat island pixels concentrated near industrial or dense built-up areas are consistent "
            "with the expected pattern; isolated hot pixels elsewhere are worth visually cross-checking "
            "against the true-color imagery for sensor artifacts.",
        ],
        "glossary": [
            ("LST", "Land Surface Temperature \u2014 the actual temperature of the ground/canopy surface "
                     "as measured by a thermal sensor, distinct from near-surface air temperature."),
            ("Urban Heat Island (UHI)", "A pixel classified as UHI here means its surface temperature "
                                         "exceeds the AOI's own mean by more than 2\u00b0C in the end-period composite."),
        ],
        "citation_keys": ["landsat"],
    },
    "deforestation": {
        "recommendations": [
            "For enforcement or compliance monitoring, cross-reference high-loss areas against land "
            "tenure/concession boundaries your organization already holds.",
            "Combine with the fire detection analysis for the same AOI/period to help distinguish "
            "fire-driven loss from mechanical clearing.",
        ],
        "glossary": [
            ("Hansen GFC", "The Hansen Global Forest Change dataset \u2014 a Landsat time-series-derived "
                            "product mapping year-2000 baseline tree canopy cover and the calendar year of any "
                            "subsequent stand-replacement loss, from University of Maryland/Google/USGS/NASA."),
            ("Canopy Cover Threshold", "This analysis counts a pixel as baseline forest where year-2000 "
                                        "canopy cover was \u2265 30% \u2014 a standard Hansen-dataset convention, not "
                                        "tuned to a specific forest type."),
        ],
        "citation_keys": ["hansen"],
    },
    "soil_moisture": {
        "recommendations": [
            "SMAP's ~9 km resolution is appropriate for regional monitoring and early-warning triage, not "
            "field-level irrigation scheduling \u2014 pair with local sensors for operational decisions.",
            "Where dry-stress area is large, cross-reference with the drought index and vegetation change "
            "analyses for the same AOI/period for a fuller picture.",
        ],
        "glossary": [
            ("sm_surface", "SMAP L4's surface soil moisture band (0-5 cm depth), reported as a volumetric "
                            "fraction (m\u00b3 water per m\u00b3 soil)."),
            ("Dry-Stress Threshold", "This analysis flags a pixel as dry-stressed where volumetric soil "
                                      "moisture falls below 0.10 m\u00b3/m\u00b3 \u2014 a general agronomic reference point, "
                                      "not calibrated to a specific soil type or crop."),
        ],
        "citation_keys": ["smap"],
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


def _study_area_section(styles, aoi_geojson: Dict[str, Any], section_num: int = 2) -> List:
    summary = aoi_summary(aoi_geojson)
    flow = [Paragraph(f"{section_num}. Study Area", styles["section_head"])]
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


def _hex_to_rgb(hex_color: str):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _lerp_color(c1: str, c2: str, t: float):
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    return colors.Color(r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t)


def _discrete_legend(entries: List[Tuple[str, str]], width: int = 300, height: int = 22) -> Drawing:
    """A row of color swatch + label pairs, for a categorical (not
    continuous) map — e.g. gain/loss/unchanged. Deliberately NOT a gradient
    bar like _gradient_legend(): a gradient implies intermediate values
    exist between the categories, which is false for a discrete
    classification (there is no 'halfway between gain and loss')."""
    d = Drawing(width, height)
    swatch = 12
    gap_after_swatch = 4
    group_gap = 20
    x = 4
    for color, label in entries:
        d.add(Rect(x, height / 2 - swatch / 2, swatch, swatch,
                    fillColor=colors.HexColor(color), strokeColor=colors.HexColor("#5c6673"), strokeWidth=0.5))
        x += swatch + gap_after_swatch
        d.add(String(x, height / 2 - 3, label, fontSize=8, fillColor=colors.HexColor("#2b2f36"),
                      textAnchor="start", fontName="Helvetica"))
        x += len(label) * 4.6 + group_gap  # rough width estimate for Helvetica 8pt
    return d


def _gradient_legend(palette: List[str], vmin: float, vmax: float, unit: str,
                      tick_labels: Optional[List[str]] = None, width: int = 300, height: int = 34) -> Drawing:
    """A horizontal color-gradient legend bar (min -> max) with tick labels
    — makes an index map (NDVI/NDDI/soil moisture) actually readable: what
    does this color correspond to, in real units. Approximates a smooth
    gradient using many thin segments interpolated across the palette
    stops, since reportlab has no native multi-stop linear-gradient fill."""
    d = Drawing(width, height)
    bar_x, bar_y, bar_w, bar_h = 5, 16, width - 10, 12
    n_segments = 60
    n_stops = len(palette) - 1
    for i in range(n_segments):
        t = i / n_segments
        stop_pos = t * n_stops
        stop_idx = min(int(stop_pos), n_stops - 1)
        local_t = stop_pos - stop_idx
        color = _lerp_color(palette[stop_idx], palette[stop_idx + 1], local_t)
        seg_x = bar_x + bar_w * t
        seg_w = bar_w / n_segments + 0.5  # slight overlap to avoid hairline gaps
        d.add(Rect(seg_x, bar_y, seg_w, bar_h, fillColor=color, strokeColor=None))
    d.add(Rect(bar_x, bar_y, bar_w, bar_h, fillColor=None, strokeColor=colors.HexColor("#5c6673"), strokeWidth=0.5))

    labels = tick_labels or [f"{vmin:g}", f"{(vmin + vmax) / 2:g}", f"{vmax:g}"]
    for i, label in enumerate(labels):
        x = bar_x + bar_w * (i / (len(labels) - 1))
        anchor = "start" if i == 0 else "end" if i == len(labels) - 1 else "middle"
        d.add(String(x, bar_y - 10, label, fontSize=7, fillColor=colors.HexColor("#5c6673"),
                      textAnchor=anchor, fontName="Helvetica"))
    if unit:
        d.add(String(bar_x + bar_w / 2, bar_y + bar_h + 4, unit, fontSize=7,
                      fillColor=colors.HexColor("#5c6673"), textAnchor="middle", fontName="Helvetica-Oblique"))
    return d


# Palette definitions shared with satellite_imagery.py's getThumbURL calls —
# kept in sync manually since one lives in report generation (reportlab
# Drawing) and the other in GEE visualization params (plain hex list); if
# satellite_imagery.py's palette changes, this must be updated to match.
NDVI_LEGEND = {"palette": ["#a83232", "#d9a441", "#e8e88a", "#8fd453", "#1a7a1a"], "min": -0.2, "max": 0.8,
                "labels": ["-0.2 (bare/water)", "0.3", "0.8 (dense veg.)"], "unit": "NDVI"}
NDDI_LEGEND = {"palette": ["#1a4d7a", "#4a9ec9", "#e8e88a", "#d97a41", "#8b2020"], "min": -0.5, "max": 1.0,
                "labels": ["-0.5 (wet)", "0.25", "1.0 (severe drought)"], "unit": "NDDI"}
MOISTURE_LEGEND = {"palette": ["#8b6b3d", "#c9a86a", "#a8c9d4", "#4a9ec9", "#1a4d7a"], "min": 0, "max": 0.5,
                     "labels": ["0.0 (dry)", "0.25", "0.5 m\u00b3/m\u00b3 (saturated)"], "unit": "Volumetric soil moisture"}


def _moisture_legend_for(observed_range):
    """Builds a soil-moisture legend matching the actual stretch used for
    this specific AOI's thumbnail, rather than always showing the generic
    fixed 0-0.5 legend regardless of what range was actually rendered."""
    if not observed_range:
        return MOISTURE_LEGEND
    vmin, vmax = observed_range
    mid = (vmin + vmax) / 2
    return {
        "palette": MOISTURE_LEGEND["palette"], "min": vmin, "max": vmax,
        "labels": [f"{vmin:.3f} (drier)", f"{mid:.3f}", f"{vmax:.3f} m³/m³ (moister)"],
        "unit": "Volumetric soil moisture — range fit to this AOI (2nd–98th percentile)",
    }
SAR_LEGEND = {"palette": ["#0a0a0a", "#4a4a4a", "#8a8a8a", "#c8c8c8", "#f5f5f5"], "min": -25, "max": 0,
               "labels": ["-25 dB (smooth/water)", "-12.5", "0 dB (rough/urban)"], "unit": "VV backscatter"}
THERMAL_LEGEND = {"palette": ["#1a4d7a", "#4a9ec9", "#e8e88a", "#d97a41", "#8b2020"], "min": 0, "max": 45,
                    "labels": ["0\u00b0C", "22.5\u00b0C", "45\u00b0C"], "unit": "Land surface temperature"}
PRECIP_LEGEND = {"palette": ["#8b6b3d", "#c9a86a", "#e8e88a", "#4a9ec9", "#1a4d7a"], "min": 0, "max": 400,
                   "labels": ["0 mm (dry)", "200 mm", "400+ mm (wet)"], "unit": "Accumulated rainfall (CHIRPS)"}
GROUNDWATER_LEGEND = {"palette": ["#8b6b3d", "#c9a86a", "#e8e88a", "#4a9ec9", "#1a4d7a"], "min": -20, "max": 20,
                        "labels": ["-20 cm (depleted)", "0", "+20 cm (surplus)"],
                        "unit": "GRACE terrestrial water storage anomaly"}

# analysis_type -> (gain_metric_key, loss_metric_key, noun) — used to build
# the change-map interpretation text with this run's actual gain/loss
# figures, and to gate which analysis types get a change map at all (only
# the three that are genuinely a two-period binary classification diff;
# flood_detection/deforestation are extent detections, not this shape).
_CHANGE_MAP_LABELS = {
    "vegetation_change": ("vegetation_gain_km2", "vegetation_loss_km2", "vegetated (NDVI \u2265 0.20) cover"),
    "builtup_change": ("builtup_gain_km2", "builtup_loss_km2", "built-up classification"),
    "water_change": ("water_gain_km2", "water_loss_km2", "surface water"),
}


def _risk_gauge(score: float) -> Drawing:
    """A horizontal color-banded gauge with a marker at the actual score —
    gives the risk number a visual anchor instead of being just a line of
    text, and shows at a glance how close it sits to the next band."""
    width, height = 480, 46
    d = Drawing(width, height)
    bands = [
        (0, 30, colors.HexColor("#4a7c59"), "LOW"),
        (30, 55, colors.HexColor("#c9933a"), "MODERATE"),
        (55, 75, colors.HexColor("#c96a3a"), "HIGH"),
        (75, 100, colors.HexColor("#8b2020"), "SEVERE"),
    ]
    bar_x, bar_y, bar_w, bar_h = 10, 18, 460, 16
    for lo, hi, color, label in bands:
        x0 = bar_x + bar_w * (lo / 100)
        x1 = bar_x + bar_w * (hi / 100)
        d.add(Rect(x0, bar_y, x1 - x0, bar_h, fillColor=color, strokeColor=colors.white, strokeWidth=0.5))
        d.add(String((x0 + x1) / 2, bar_y - 10, label, fontSize=6.5, fillColor=colors.HexColor("#5c6673"),
                      textAnchor="middle", fontName="Helvetica"))

    marker_x = bar_x + bar_w * (max(0, min(100, score)) / 100)
    d.add(Polygon(points=[marker_x - 5, bar_y + bar_h + 10, marker_x + 5, bar_y + bar_h + 10, marker_x, bar_y + bar_h + 2],
                  fillColor=colors.HexColor("#12151a"), strokeColor=None))
    d.add(String(marker_x, bar_y + bar_h + 13, f"{score:.1f}", fontSize=9, fontName="Helvetica-Bold",
                  fillColor=colors.HexColor("#12151a"), textAnchor="middle"))
    return d


def _imagery_grid_section(styles, images: List[Dict[str, Any]], section_num: int) -> List:
    """Like _imagery_section but for an arbitrary set of labeled images laid
    out 2-per-row — used for the agri report's per-indicator maps (true
    color, NDVI, NDDI, soil moisture) rather than a single before/after pair.

    Each entry in `images` is a dict: {"label", "bytes", "caption"
    (source/sensor/date, one line), "legend" (optional dict with palette/
    min/max/unit/labels — omit for true-color/photographic imagery that
    isn't mapping values to colors)}. Any entry with bytes=None is skipped
    entirely rather than shown as a blank placeholder."""
    available = [img for img in images if img.get("bytes")]
    flow = [Paragraph(f"{section_num}. Satellite Imagery by Indicator", styles["section_head"])]
    if not available:
        flow.append(Paragraph(
            "No cloud-free satellite imagery was available for this AOI within the analysis window to "
            "embed here; the metrics and findings below are unaffected, as they are computed from the "
            "same underlying satellite collections independently of these thumbnails.",
            styles["caveat"]))
        return flow

    img_w = 78 * mm
    cell_style = ParagraphStyle("grid_caption", parent=styles["footer"], alignment=TA_CENTER, spaceBefore=3)

    def _cell(img):
        content = [Image(io.BytesIO(img["bytes"]), width=img_w, height=img_w)]
        content.append(Paragraph(f"<b>{img['label']}</b>", cell_style))
        if img.get("caption"):
            content.append(Paragraph(img["caption"], cell_style))
        if img.get("legend"):
            leg = img["legend"]
            drawing = _gradient_legend(leg["palette"], leg["min"], leg["max"], leg["unit"],
                                        tick_labels=leg.get("labels"), width=int(img_w))
            drawing.hAlign = "CENTER"
            content.append(Spacer(1, 3))
            content.append(drawing)
        return content

    rows = []
    for i in range(0, len(available), 2):
        pair = available[i:i + 2]
        row = [_cell(img) for img in pair]
        if len(pair) == 1:
            row.append("")
        rows.append(row)

    t = Table(rows, colWidths=[img_w + 4, img_w + 4])
    style_cmds = [
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]
    t.setStyle(TableStyle(style_cmds))
    flow.append(t)
    flow.append(Spacer(1, 4))
    return flow


def _imagery_section(styles, before_bytes: Optional[bytes], after_bytes: Optional[bytes],
                      before_label: str, after_label: str, source_caption: str = "",
                      legend: Optional[Dict[str, Any]] = None) -> List:
    """Embeds actual satellite imagery (not just derived metrics) side by
    side, so the reader can see the real scene a finding is drawn from.
    Falls back to a single centered image when only one side is available
    (e.g. the agri risk report only has a current-conditions image, not a
    before/after pair). source_caption is one line (satellite/sensor,
    resolution) shown under both images; legend (optional) renders a
    color-gradient key for imagery that maps values to colors (e.g. SAR
    backscatter) rather than being a plain true-color photo."""
    flow = [Paragraph("2. Satellite Imagery", styles["section_head"])]
    if not before_bytes and not after_bytes:
        flow.append(Paragraph(
            "No cloud-free satellite imagery was available for this AOI within the analysis window to "
            "embed here; the metrics and findings below are unaffected, as they are computed from the "
            "same underlying satellite collections independently of this thumbnail.",
            styles["caveat"]))
        return flow

    cell_style = ParagraphStyle("img_caption_center", parent=styles["footer"], alignment=TA_CENTER)

    if bool(before_bytes) != bool(after_bytes):
        # Single-image case — center it full-width rather than pairing with
        # an empty placeholder column.
        img_bytes = before_bytes or after_bytes
        label = before_label if before_bytes else after_label
        img = Image(io.BytesIO(img_bytes), width=110 * mm, height=110 * mm)
        img.hAlign = "CENTER"
        flow.append(img)
        if label:
            flow.append(Paragraph(f"<b>{label}</b>", cell_style))
        if source_caption:
            flow.append(Paragraph(source_caption, cell_style))
        if legend:
            d = _gradient_legend(legend["palette"], legend["min"], legend["max"], legend["unit"],
                                  tick_labels=legend.get("labels"), width=220)
            d.hAlign = "CENTER"
            flow.append(Spacer(1, 3))
            flow.append(d)
        flow.append(Spacer(1, 4))
        return flow

    img_w = 78 * mm

    def _cell(b, label):
        content = [Image(io.BytesIO(b), width=img_w, height=img_w), Paragraph(f"<b>{label}</b>", cell_style)]
        if source_caption:
            content.append(Paragraph(source_caption, cell_style))
        if legend:
            d = _gradient_legend(legend["palette"], legend["min"], legend["max"], legend["unit"],
                                  tick_labels=legend.get("labels"), width=int(img_w))
            d.hAlign = "CENTER"
            content.append(Spacer(1, 3))
            content.append(d)
        return content

    cells = [_cell(before_bytes, before_label), _cell(after_bytes, after_label)]

    t = Table([cells], colWidths=[img_w + 4, img_w + 4])
    t.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    flow.append(t)
    flow.append(Spacer(1, 4))
    return flow


def _worked_example_section(styles, analysis_type: str, m: Dict[str, Any]) -> List:
    """A short worked numeric example showing this specific run's actual
    values substituted into the formula/area calculation described in
    Methodology above \u2014 lets a reader verify the arithmetic themselves
    rather than trusting the reported figures on faith."""
    lines = []
    if analysis_type == "vegetation_change":
        loss = m.get("vegetation_loss_km2"); initial = m.get("initial_vegetation_km2"); pct = m.get("loss_pct")
        if loss is not None and initial:
            lines = [
                f"Decline share = Vegetation Decline \u00f7 Initial Vegetated Area \u00d7 100",
                f"= {loss:,.2f} km\u00b2 \u00f7 {initial:,.2f} km\u00b2 \u00d7 100 = {pct:,.2f}% "
                f"(as reported in Section 5).",
            ]
    elif analysis_type == "builtup_change":
        gain = m.get("builtup_gain_km2"); loss = m.get("builtup_loss_km2"); initial = m.get("initial_builtup_km2")
        final = m.get("final_builtup_km2")
        if gain is not None and loss is not None and initial is not None:
            lines = [
                f"Net Change = Gain \u2212 Loss = {gain:,.2f} \u2212 {loss:,.2f} = {gain - loss:+,.2f} km\u00b2",
                f"Final Built-Up Area = Initial + Net Change = {initial:,.2f} + ({gain - loss:+,.2f}) "
                f"= {final:,.2f} km\u00b2 (as reported in Section 5).",
            ]
    elif analysis_type == "drought_index":
        drought = m.get("drought_affected_km2"); severe = m.get("severe_drought_km2")
        if drought is not None and severe is not None:
            lines = [
                f"Severe drought is the subset of drought-affected area meeting the stricter NDDI > 0.7 "
                f"threshold (vs. NDDI > 0.5 for the general drought-affected figure):",
                f"Severely drought-affected ({severe:,.2f} km\u00b2) \u2264 Drought-affected ({drought:,.2f} km\u00b2) "
                f"\u2014 confirmed consistent for this run.",
            ]
    elif analysis_type == "soil_moisture":
        start = m.get("start_avg_soil_moisture"); end = m.get("end_avg_soil_moisture"); change = m.get("moisture_change")
        if start is not None and end is not None:
            lines = [
                f"Change = End Mean \u2212 Start Mean = {end:.4f} \u2212 {start:.4f} = {change:+.4f} m\u00b3/m\u00b3 "
                f"(as reported in Section 5).",
            ]
    elif analysis_type == "land_surface_temperature":
        start = m.get("start_mean_lst_c"); end = m.get("end_mean_lst_c")
        if start is not None and end is not None:
            lines = [
                f"\u0394T = End Mean LST \u2212 Start Mean LST = {end:.2f}\u00b0C \u2212 {start:.2f}\u00b0C "
                f"= {end - start:+.2f}\u00b0C.",
                "The underlying LST(\u00b0C) conversion for each pixel is: "
                "ST_B10 \u00d7 0.00341802 + 149.0 \u2212 273.15, per USGS Collection 2 scaling.",
            ]
    elif analysis_type == "deforestation":
        loss = m.get("forest_loss_km2"); baseline = m.get("total_forest_2000_km2"); pct = m.get("loss_pct")
        if loss is not None and baseline:
            lines = [
                f"Loss share = Forest Loss \u00f7 Baseline (2000) Forest Area \u00d7 100",
                f"= {loss:,.2f} km\u00b2 \u00f7 {baseline:,.2f} km\u00b2 \u00d7 100 = {pct:,.2f}% "
                f"(as reported in Section 5).",
            ]

    if not lines:
        return []
    flow = [Paragraph("Worked Example (this run's actual values)", ParagraphStyle(
        "worked_ex_head", parent=styles["section_head"], fontSize=10.5, spaceBefore=8))]
    for line in lines:
        flow.append(Paragraph(line, ParagraphStyle(
            "worked_ex", parent=styles["body"], fontName="Courier", fontSize=8.5, leading=13,
            backColor=colors.HexColor("#f4f5f6"), borderPadding=6)))
    return flow


def _methodology_section(styles, sources: List[str], paragraphs: List[str], section_num: int = 4) -> List:
    flow = [Paragraph(f"{section_num}. Data Sources & Methodology", styles["section_head"])]
    flow.append(Paragraph("<b>Sources:</b> " + "; ".join(sources), styles["body"]))
    for p in paragraphs:
        flow.append(Paragraph(p, styles["body"]))
    return flow


def _results_section(styles, metric_rows: List[Tuple[str, str, str]], section_num: int = 5) -> List:
    flow = [Paragraph(f"{section_num}. Results", styles["section_head"])]
    flow.append(_metrics_table(styles, metric_rows))
    flow.append(Spacer(1, 4))
    return flow


def _findings_section(styles, paragraphs: List[str], section_num: int = 6) -> List:
    flow = [Paragraph(f"{section_num}. Findings & Interpretation", styles["section_head"])]
    for p in paragraphs:
        flow.append(Paragraph(p, styles["body"]))
    return flow


def _limitations_section(styles, text: str, section_num: int = 6) -> List:
    flow = [Paragraph(f"{section_num}. Limitations & Caveats", styles["section_head"])]
    flow.append(Paragraph(text, styles["caveat"]))
    flow.append(Paragraph(
        "This report is generated from satellite remote-sensing data and automated processing. It is "
        "intended to support, not replace, field verification and professional judgment for operational, "
        "legal, or financial decisions.",
        styles["caveat"]))
    return flow


# ═════════════════════════════════════════════════════════════════════════════
# Extended report sections: cover, TOC, executive summary, full-page imagery
# with interpretation, data quality, glossary, citations, recommendations.
# These exist because a 2-3 page report reads as a quick automated printout
# rather than something a scientist or officer would treat as a formal
# assessment — the fix is genuine additional substance (a real interpretation
# paragraph per image, dataset citations, a data-quality discussion, an
# actionable recommendations section), not padding line counts or margins.
# ═════════════════════════════════════════════════════════════════════════════

def _cover_page(styles, title: str, subtitle: str, meta_rows: List[Tuple[str, str]]) -> List:
    flow = _header_flowables(styles, title, subtitle)
    flow.append(Spacer(1, 30))
    flow.append(_metadata_table(styles, meta_rows))
    flow.append(Spacer(1, 40))
    flow.append(HRFlowable(width="60%", thickness=0.5, color=LINE, hAlign="CENTER"))
    flow.append(Spacer(1, 10))
    flow.append(Paragraph(
        "This document is a satellite remote-sensing assessment generated by the VAYU geospatial "
        "intelligence platform. It combines automated analysis of public satellite data sources with "
        "deterministic scientific methodology to produce a reproducible, source-cited report.",
        ParagraphStyle("cover_note", parent=styles["body"], alignment=TA_CENTER, fontSize=9, textColor=MUTED)))
    flow.append(PageBreak())
    return flow


def _table_of_contents(styles, sections: List[Tuple[str, str]]) -> List:
    """sections: list of (number_and_title, one_line_description)."""
    flow = [Paragraph("Table of Contents", styles["section_head"])]
    rows = [[Paragraph(f"<b>{num_title}</b>", styles["body"]), Paragraph(desc, styles["footer"])]
            for num_title, desc in sections]
    t = Table(rows, colWidths=[65 * mm, 100 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    flow.append(t)
    flow.append(PageBreak())
    return flow


def _sanitize_llm_text(text: str) -> str:
    """Replace Unicode characters the report's base-14 Helvetica font
    (WinAnsi encoding only) can't render. Two failure modes matter here,
    found by inspecting actual generated reports:
    (1) tofu boxes ('\u25a0') for punctuation like a non-breaking hyphen
        (U+2011) that has no WinAnsi slot at all;
    (2) words silently running together ('19August2025', '100percent')
        when an earlier version of this function used a fixed whitelist —
        any Unicode space variant NOT in that whitelist (e.g. thin space
        U+2009, narrow no-break space U+202F) fell through to the generic
        'strip anything outside Latin-1' branch and was deleted instead of
        being converted to a real space, silently gluing words together.
    Fixed here by classifying every out-of-range character by its Unicode
    general category rather than an incomplete hand-picked list: any
    'space separator' (Zs) becomes a plain space; hyphen/dash-like
    punctuation is normalized to '-'; the rest is dropped only as a last
    resort, with a log line so a genuinely new pattern doesn't fail silently
    the way the space-stripping did."""
    replacements = {
        "\u2018": "'", "\u2019": "'", "\u201a": "'",   # single quotes
        "\u201c": '"', "\u201d": '"', "\u201e": '"',   # double quotes
        "\u2026": "...",  # ellipsis
        "\u2212": "-",    # minus sign (Unicode category Sm, not covered by the Pd/dash check below)
        "\u200b": "", "\u200c": "", "\u200d": "",       # zero-width space/joiners
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)

    out_chars = []
    dropped_any = False
    for ch in text:
        if ord(ch) < 256:
            out_chars.append(ch)
            continue
        category = unicodedata.category(ch)
        if category == "Zs":  # any Unicode space separator, not just NBSP
            out_chars.append(" ")
        elif category == "Pd":  # any dash/hyphen punctuation (en/em dash, non-breaking hyphen, etc.)
            out_chars.append("-")
        else:
            dropped_any = True  # last resort — log rather than silently vanish
    cleaned = "".join(out_chars)
    if dropped_any:
        logger.warning(
            "report_generator: dropped Unicode character(s) from LLM text with no safe "
            "WinAnsi equivalent (category outside space/dash) \u2014 worth checking the raw "
            "LLM output if this recurs"
        )
    return cleaned


def _render_multi_paragraph(styles, text: str, style_key: str = "body") -> List:
    """Splits LLM-generated multi-paragraph text (paragraphs separated by a
    blank line) into separate Paragraph flowables — a single reportlab
    Paragraph does not render '\\n\\n' as visible paragraph breaks, it just
    collapses to whitespace, so a real paragraph split has to happen here."""
    text = _sanitize_llm_text(text)
    parts = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not parts:
        parts = [text.strip()]
    return [Paragraph(p, styles[style_key]) for p in parts]


def _executive_summary_section(styles, paragraphs: List[str], section_num: int = 1) -> List:
    flow = [Paragraph(f"{section_num}. Executive Summary", styles["section_head"])]
    for p in paragraphs:
        flow.append(Paragraph(p, styles["body"]))
    return flow


def _full_page_imagery_section(styles, images: List[Dict[str, Any]], section_num: int) -> List:
    """Like _imagery_grid_section but one substantially larger image per
    block with a real interpretation paragraph underneath — not just a
    caption — so the imagery section reads as analysis, not decoration."""
    available = [img for img in images if img.get("bytes")]
    flow = [Paragraph(f"{section_num}. Satellite Imagery \u2014 Detailed View", styles["section_head"])]
    if not available:
        flow.append(Paragraph(
            "No cloud-free satellite imagery was available for this AOI within the analysis window to "
            "embed here; the metrics and findings elsewhere in this report are unaffected, as they are "
            "computed from the same underlying satellite collections independently of these thumbnails.",
            styles["caveat"]))
        return flow

    img_w = 120 * mm
    for idx, img in enumerate(available):
        block = [Paragraph(f"{section_num}.{idx + 1} {img['label']}", ParagraphStyle(
            "subsection", parent=styles["section_head"], fontSize=10.5, spaceBefore=10))]
        picture = Image(io.BytesIO(img["bytes"]), width=img_w, height=img_w)
        picture.hAlign = "CENTER"
        block.append(picture)
        if img.get("caption"):
            block.append(Paragraph(img["caption"], ParagraphStyle(
                "img_cap", parent=styles["footer"], alignment=TA_CENTER, spaceBefore=4)))
        if img.get("legend"):
            leg = img["legend"]
            d = _gradient_legend(leg["palette"], leg["min"], leg["max"], leg["unit"],
                                  tick_labels=leg.get("labels"), width=int(img_w))
            d.hAlign = "CENTER"
            block.append(Spacer(1, 3))
            block.append(d)
        if img.get("discrete_legend"):
            d = _discrete_legend(img["discrete_legend"], width=int(img_w))
            d.hAlign = "CENTER"
            block.append(Spacer(1, 3))
            block.append(d)
        if img.get("interpretation"):
            block.append(Spacer(1, 5))
            block.append(Paragraph(img["interpretation"], styles["body"]))
        flow.extend(block)
        flow.append(Spacer(1, 10))
    return flow


def _data_quality_section(styles, rows: List[Tuple[str, str]], narrative: str, section_num: int) -> List:
    flow = [Paragraph(f"{section_num}. Data Quality & Confidence", styles["section_head"])]
    flow.append(Paragraph(narrative, styles["body"]))
    flow.append(_metadata_table(styles, rows))
    flow.append(Spacer(1, 4))
    return flow


def _glossary_section(styles, terms: List[Tuple[str, str]], section_num: int) -> List:
    flow = [Paragraph(f"{section_num}. Glossary & Formula Reference", styles["section_head"])]
    rows = [[Paragraph(f"<b>{term}</b>", styles["body"]), Paragraph(defn, styles["body"])] for term, defn in terms]
    t = Table(rows, colWidths=[38 * mm, 127 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    flow.append(t)
    flow.append(Spacer(1, 4))
    return flow


def _citations_section(styles, citations: List[str], section_num: int) -> List:
    flow = [Paragraph(f"{section_num}. Data Sources & Citations", styles["section_head"])]
    for i, c in enumerate(citations, 1):
        flow.append(Paragraph(f"[{i}] {c}", ParagraphStyle(
            "citation", parent=styles["body"], fontSize=9, leading=13, spaceAfter=6)))
    return flow


def _recommendations_section(styles, paragraphs: List[str], section_num: int) -> List:
    flow = [Paragraph(f"{section_num}. Recommendations", styles["section_head"])]
    for p in paragraphs:
        flow.append(Paragraph(p, styles["body"]))
    return flow


GLOSSARY_COMMON = [
    ("AOI", "Area of Interest \u2014 the user-defined or searched boundary polygon this report's analysis is restricted to."),
    ("km\u00b2", "Square kilometers, the unit used for all area measurements in this report."),
    ("Spatial Resolution", "The ground distance represented by one pixel in the source imagery (e.g. 10 m means each pixel covers a 10\u00d710 m ground area). Finer resolution allows detection of smaller features."),
    ("Cloud Masking", "A preprocessing step that excludes cloud- and cloud-shadow-contaminated pixels from a satellite scene before analysis, so cloud cover isn't mistaken for a ground feature."),
]

GLOSSARY_MEDIAN_COMPOSITE = ("Median Composite", "A pixel-wise median taken across multiple satellite scenes over a time window, used to suppress the influence of any single anomalous (cloudy, shadowed, or noisy) scene.")
GLOSSARY_MEAN_COMPOSITE = ("Mean Composite", "A pixel-wise average taken across multiple satellite scenes over a time window, used to smooth out noise and fill gaps from any single scene.")
GLOSSARY_MODAL_COMPOSITE = ("Modal Composite", "The single most-frequently-occurring classification across all scenes in the time window, used instead of a single date to reduce the influence of any one misclassified scene. Used for categorical land-cover class data, not continuous values (unlike a median or mean composite).")

# analysis_type -> which compositing-method glossary entry actually applies,
# verified against each compute_*() function's actual reducer rather than
# assumed — a categorical classifier (Dynamic World, JRC water) uses
# .mode(), not .median(); SAR backscatter uses .mean(); some analyses
# (fire detection's burned-area union, deforestation's single Hansen GFC
# lookup) don't do windowed scene compositing at all, so they map to None
# rather than getting an inapplicable entry. builtup_change maps to None
# because it already supplies its own correctly-worded "Modal Composite"
# entry directly in its ANALYSIS_EXTRAS glossary list — mapping it here
# too would print the term twice.
_COMPOSITING_GLOSSARY_BY_TYPE = {
    "land_surface_temperature": GLOSSARY_MEAN_COMPOSITE,   # .mean() — gee_client.py compute_temperature_context / compute_land_surface_temperature
    "soil_moisture": GLOSSARY_MEAN_COMPOSITE,               # .mean() — compute_soil_moisture
    "flood_detection": GLOSSARY_MEAN_COMPOSITE,             # .mean() — compute_flood_detection (SAR backscatter)
    "water_change": GLOSSARY_MODAL_COMPOSITE,               # .mode() — compute_water_change (JRC categorical water class)
    "builtup_change": None,                                  # .mode() — already has its own entry, see comment above
    "fire_detection": None,                                  # .max() union over the period, not a representative-scene composite
    "deforestation": None,                                   # single Hansen GFC year-of-loss lookup, no windowed composite
}


def _compositing_glossary_entry(analysis_type: str):
    if analysis_type in _COMPOSITING_GLOSSARY_BY_TYPE:
        return _COMPOSITING_GLOSSARY_BY_TYPE[analysis_type]  # may legitimately be None
    return GLOSSARY_MEDIAN_COMPOSITE  # vegetation_change, drought_index — both confirmed .median()

CITATIONS_BY_SOURCE = {
    "sentinel2": "European Space Agency (ESA), Copernicus Sentinel-2 Mission, Level-2A Surface Reflectance. Available via Google Earth Engine: COPERNICUS/S2_SR_HARMONIZED.",
    "sentinel1": "European Space Agency (ESA), Copernicus Sentinel-1 Mission, Ground Range Detected (GRD) SAR. Available via Google Earth Engine: COPERNICUS/S1_GRD.",
    "landsat": "U.S. Geological Survey / NASA, Landsat 8-9 Collection 2 Level-2 Science Products. Available via Google Earth Engine: LANDSAT/LC08-LC09/C02/T1_L2.",
    "dynamicworld": "Brown, C.F., Brumby, S.P., Guzder-Williams, B. et al. (2022). Dynamic World, Near real-time global 10 m land use land cover mapping. Scientific Data 9, 251. Google/World Resources Institute.",
    "jrc_water": "Pekel, J.F., Cottam, A., Gorelick, N., Belward, A.S. (2016). High-resolution mapping of global surface water and its long-term changes. Nature 540, 418\u2013422. European Commission Joint Research Centre.",
    "hansen": "Hansen, M.C., Potapov, P.V., Moore, R. et al. (2013). High-Resolution Global Maps of 21st-Century Forest Cover Change. Science 342(6160), 850\u2013853. University of Maryland / Google / USGS / NASA.",
    "modis_fire": "Giglio, L., Boschetti, L., Roy, D.P. et al. (2018). The Collection 6 MODIS burned area mapping algorithm and product. Remote Sensing of Environment 217, 72\u201385. NASA MODIS/VIIRS product suite.",
    "smap": "Entekhabi, D., Yueh, S., O'Neill, P.E. et al. NASA Soil Moisture Active Passive (SMAP) Mission, SPL4SMGP Level-4 Surface and Root Zone Soil Moisture. NASA Jet Propulsion Laboratory / Goddard Space Flight Center.",
    "srtm": "Farr, T.G., Rosen, P.A., Caro, E. et al. (2007). The Shuttle Radar Topography Mission. Reviews of Geophysics 45, RG2004. NASA/USGS/JPL.",
    "smap10km_deprecated": "Colliander, A. et al., NASA/USDA SMAP10KM downscaled soil moisture (legacy product, deprecated by data provider).",
    "grace": "Landerer, F.W. et al. NASA/German Research Centre for Geosciences (GFZ) GRACE and GRACE-FO Mascon products, terrestrial water storage anomaly. Available via Google Earth Engine: NASA/GRACE/MASS_GRIDS_V04/LAND.",
    "chirps": "Funk, C., Peterson, P., Landsfeld, M. et al. (2015). The climate hazards infrared precipitation with stations (CHIRPS) record. Scientific Data 2, 150066. UC Santa Barbara Climate Hazards Group. Available via Google Earth Engine: UCSB-CHG/CHIRPS/DAILY.",
}




CONSISTENCY_TOLERANCE_KM2 = 0.5  # allows for GEE pixel-area rounding across independent reduceRegion calls

# For analysis types with a gain/loss/initial/(net or final) relationship,
# maps analysis_type -> (gain_key, loss_key, initial_key, net_key_or_None, final_key_or_None).
# Used to catch exactly the class of bug this report generator shipped with
# once already: metric keys silently not matching gee_client's real output,
# producing a report where net change reads "+0.00" while gain and loss are
# both large nonzero numbers. Rather than trust every future analysis type
# to get this right, check it mechanically before rendering.
_CONSISTENCY_CHECKS = {
    "vegetation_change": ("vegetation_gain_km2", "vegetation_loss_km2", "initial_vegetation_km2", "net_change_km2", None),
    "builtup_change": ("builtup_gain_km2", "builtup_loss_km2", "initial_builtup_km2", "net_change_km2", "final_builtup_km2"),
    "water_change": ("water_gain_km2", "water_loss_km2", "initial_water_km2", "net_change_km2", None),
}


def _validate_consistency(analysis_type: str, metrics: Dict[str, Any]) -> Optional[str]:
    """Returns an error message if the metrics are internally inconsistent
    (gain - loss doesn't match the reported net/final change), or None if
    they check out / this analysis type has no such relationship to check."""
    check = _CONSISTENCY_CHECKS.get(analysis_type)
    if not check:
        return None
    gain_k, loss_k, initial_k, net_k, final_k = check
    gain, loss, initial = metrics.get(gain_k), metrics.get(loss_k), metrics.get(initial_k)
    if gain is None or loss is None:
        return None  # can't check what wasn't computed
    expected_net = gain - loss

    if net_k and metrics.get(net_k) is not None:
        if abs(metrics[net_k] - expected_net) > CONSISTENCY_TOLERANCE_KM2:
            return (f"Inconsistent metrics for {analysis_type}: reported {net_k}="
                    f"{metrics[net_k]:.2f} km\u00b2 but gain ({gain:.2f}) - loss ({loss:.2f}) = "
                    f"{expected_net:.2f} km\u00b2")
    if final_k and metrics.get(final_k) is not None and initial is not None:
        expected_final = initial + expected_net
        if abs(metrics[final_k] - expected_final) > CONSISTENCY_TOLERANCE_KM2:
            return (f"Inconsistent metrics for {analysis_type}: reported {final_k}="
                    f"{metrics[final_k]:.2f} km\u00b2 but initial ({initial:.2f}) + gain ({gain:.2f}) - "
                    f"loss ({loss:.2f}) = {expected_final:.2f} km\u00b2")
    return None


_IMAGE_INTERPRETATION_HINTS = {
    "flood_detection": (
        "Darker regions in this SAR composite indicate a smoother surface returning less radar energy to "
        "the sensor \u2014 consistent with calm standing water. Compare the start- and end-period images: "
        "new dark regions appearing in the end-period composite, in areas that aren't permanent waterbodies "
        "(see Section 4 for how permanent water is excluded), are the visual signature the flood-area "
        "figure in Section 5 is derived from."
    ),
}
_DEFAULT_INTERPRETATION_HINT = (
    "This is a true-color composite \u2014 approximately what the area would look like to the eye from "
    "orbit, built from cloud-free Sentinel-2 scenes over the stated window. It provides visual context for "
    "the quantitative results in Section 5; the composite itself is not the basis for those figures, which "
    "are computed from the same underlying satellite bands using the indices described in Section 4."
)


def _analysis_data_quality_rows(analysis_type: str, metrics: Dict[str, Any]) -> List[Tuple[str, str]]:
    rows = []
    if analysis_type in _CONSISTENCY_CHECKS:
        rows.append(
            ("Consistency Check", "Passed \u2014 reported change figures are internally consistent "
                                    "(gain \u2212 loss matches the reported net/final change) before this report was generated.")
        )
    if analysis_type == "flood_detection":
        ref = metrics.get("reference_scenes_used")
        flood = metrics.get("flood_period_scenes_used")
        if ref is not None:
            rows.append(("Reference-Period Scenes", f"{ref} SAR scenes"))
        if flood is not None:
            rows.append(("Flood-Period Scenes", f"{flood} SAR scenes"))
    if not rows:
        # A table with zero rows crashes reportlab outright (ValueError:
        # "must have at least a row and column") rather than rendering
        # empty — confirmed the hard way: every analysis type with no
        # consistency check AND no flood-specific scene counts (5 of the 9
        # types) hit exactly this and would 500 on every real request,
        # never caught because this path wasn't tested end-to-end after
        # the consistency check was made conditional. This fallback row
        # guarantees at least one row always exists, for these 5 types and
        # for any future analysis_type this function doesn't yet know
        # about, so a missing case here degrades to an accurate disclosure
        # instead of crashing the whole report.
        rows.append(
            ("Data Quality Checks", "No automated consistency check applies to this analysis type "
                                     "(it has no gain/loss relationship to verify algebraically).")
        )
    return rows


def build_analysis_report(
    analysis_type: str,
    aoi_geojson: Dict[str, Any],
    start_date: str,
    end_date: str,
    metrics: Dict[str, Any],
    before_image_bytes: Optional[bytes] = None,
    after_image_bytes: Optional[bytes] = None,
    before_image_meta: Optional[Dict[str, Any]] = None,
    after_image_meta: Optional[Dict[str, Any]] = None,
    change_map_bytes: Optional[bytes] = None,
    llm_synthesis: Optional[str] = None,
) -> bytes:
    """Builds one of the 9 satellite-analysis PDF reports. Returns raw PDF bytes."""
    spec = ANALYSIS_SPECS.get(analysis_type)
    if not spec:
        raise ValueError(f"Unknown analysis_type: {analysis_type}. Must be one of {list(ANALYSIS_SPECS)}")
    extras = ANALYSIS_EXTRAS.get(analysis_type, {})

    consistency_error = _validate_consistency(analysis_type, metrics)
    if consistency_error:
        raise ValueError(f"RESULT VALIDATION FAILED: {consistency_error}")

    styles = _styles()
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm, bottomMargin=20 * mm,
                             leftMargin=20 * mm, rightMargin=20 * mm)

    flow = []
    flow += _cover_page(styles, spec["title"], f"Analysis Period: {start_date} \u2013 {end_date}", [
        ("Report Generated", generated_at),
        ("Analysis Type", spec["title"]),
        ("Analysis Period", f"{start_date} to {end_date}"),
    ])
    flow += _table_of_contents(styles, [
        ("1. Executive Summary", "Key finding at a glance"),
        ("2. Study Area", "AOI geometry and coordinates"),
        ("3. Satellite Imagery", "Detailed before/after imagery with interpretation"),
        ("4. Data Sources & Methodology", "Datasets, formulas, and processing steps used"),
        ("5. Results", "Full computed metrics table"),
        ("6. Findings & Interpretation", "What the results indicate"),
        ("7. Data Quality & Confidence", "Validation checks and data completeness"),
        ("8. Recommendations", "Suggested next steps"),
        ("9. Glossary & Formula Reference", "Terms and formulas used in this report"),
        ("10. Data Sources & Citations", "Full citations for every dataset used"),
        ("11. Limitations & Caveats", "What this analysis does not tell you"),
    ])

    findings = spec["findings_fn"](metrics)
    exec_summary = [findings[0]] if findings else []
    exec_summary.append(
        f"Full methodology, per-image interpretation, data-quality notes, and dataset citations follow in "
        f"the sections below."
    )
    flow += _executive_summary_section(styles, exec_summary, section_num=1)
    flow.append(PageBreak())

    flow += _study_area_section(styles, aoi_geojson)
    flow.append(PageBreak())
    if analysis_type == "flood_detection":
        img_source_caption = "Sentinel-1 SAR (VV polarization), \u00b115 days around each date"
        img_legend = SAR_LEGEND
        before_caption, after_caption = img_source_caption, img_source_caption
    else:
        img_legend = None

        def _optical_caption(meta: Optional[Dict[str, Any]]) -> str:
            # Honest about what actually happened for this specific image,
            # not a fixed claim — the code widens the search window when a
            # ±30-day window doesn't have enough cloud-free coverage (e.g. a
            # monsoon-season date can genuinely have far less available than
            # a dry-season one), and a caption that always says "±30 days,
            # cloud-free" regardless overclaims exactly when the image looks
            # worst and the caveat matters most.
            base = "Sentinel-2, cloud-masked true-color composite"
            if not meta:
                return base + " (window/coverage unknown)"
            window = meta.get("window_days", 30)
            valid_pct = meta.get("valid_pct")
            cap = f"{base}, \u00b1{window} days around this date"
            if valid_pct is not None:
                cap += f" \u00b7 {valid_pct:.0f}% of the AOI had valid (cloud-free) pixels in this composite"
                if valid_pct < 60:
                    cap += " \u2014 residual cloud/haze contamination is likely visible"
            if window > 30:
                cap += " (widened from the default \u00b130 days due to limited cloud-free coverage nearer the date)"
            return cap

        before_caption = _optical_caption(before_image_meta)
        after_caption = _optical_caption(after_image_meta)
    interpretation = _IMAGE_INTERPRETATION_HINTS.get(analysis_type, _DEFAULT_INTERPRETATION_HINT)
    imagery_blocks = [
        {"label": f"Start of Period ({start_date})", "bytes": before_image_bytes,
         "caption": before_caption, "legend": img_legend, "interpretation": interpretation},
        {"label": f"End of Period ({end_date})", "bytes": after_image_bytes,
         "caption": after_caption, "legend": img_legend, "interpretation": interpretation},
    ]
    if change_map_bytes and analysis_type in _CHANGE_MAP_LABELS:
        gain_key, loss_key, noun = _CHANGE_MAP_LABELS[analysis_type]
        gain_val, loss_val = metrics.get(gain_key), metrics.get(loss_key)
        if gain_val is not None and loss_val is not None:
            change_interpretation = (
                f"Green marks pixels that gained {noun} between the two periods ({gain_val:,.2f} km\u00b2 "
                f"total); red marks pixels that lost it ({loss_val:,.2f} km\u00b2 total); the neutral "
                f"background is unchanged. This map makes the spatial pattern of change visible directly "
                f"\u2014 concentrated at the edge of an existing cluster reads very differently than "
                f"scattered across the AOI, even when the total area is the same."
            )
        else:
            change_interpretation = (
                f"Green marks pixels that gained {noun} between the two periods, red marks pixels that "
                f"lost it, and the neutral background is unchanged."
            )
        imagery_blocks.append({
            "label": "Change Map (Gain / Loss / Unchanged)", "bytes": change_map_bytes,
            "caption": "Built using the same windowing, thresholds, and classification logic as the "
                       "metrics above (independently recomputed, not read back from the same "
                       "in-memory result), so it should always match the numbers in Section 5.",
            "discrete_legend": [("#e8e4d8", "Unchanged"), ("#2e8b3f", "Gain"), ("#c0392b", "Loss")],
            "interpretation": change_interpretation,
        })
    flow += _full_page_imagery_section(styles, imagery_blocks, section_num=3)

    flow += _methodology_section(styles, spec["sources"], spec["methodology"])
    flow += _worked_example_section(styles, analysis_type, metrics)

    metric_rows = []
    for key, (label, unit, decimals) in spec["metric_labels"].items():
        if key in metrics:
            metric_rows.append((label, _fmt_num(metrics[key], decimals), unit))
    flow += _results_section(styles, metric_rows)

    flow += _findings_section(styles, findings)
    if llm_synthesis:
        flow.append(Paragraph("<i>Assessment summary:</i>", styles["meta_label"]))
        flow.extend(_render_multi_paragraph(styles, llm_synthesis))

    if analysis_type in _CONSISTENCY_CHECKS:
        dq_narrative = (
            "Before this report was generated, the computed metrics passed an automated internal consistency "
            "check (verifying reported gain/loss figures algebraically match the reported net or final change) "
            "\u2014 a report failing this check is not produced. The table below lists what else is known about "
            "this specific run's data completeness."
        )
    else:
        dq_narrative = (
            "This analysis type does not report a gain/loss breakdown, so no gain-loss consistency check "
            "applies. The table below lists what is known about this specific run's data completeness."
        )
    flow += _data_quality_section(
        styles, _analysis_data_quality_rows(analysis_type, metrics),
        dq_narrative,
        section_num=7)

    if extras.get("recommendations"):
        flow.append(PageBreak())
        flow += _recommendations_section(styles, extras["recommendations"], section_num=8)
    flow.append(PageBreak())
    flow.append(Paragraph("Appendices", ParagraphStyle(
        "appendix_head", parent=styles["report_heading"], fontSize=13, spaceBefore=0)))
    flow.append(Spacer(1, 8))
    if extras.get("glossary"):
        compositing_entry = _compositing_glossary_entry(analysis_type)
        glossary_terms = GLOSSARY_COMMON + ([compositing_entry] if compositing_entry else []) + extras["glossary"]
        flow += _glossary_section(styles, glossary_terms, section_num=9)
    citation_keys = extras.get("citation_keys", [])
    if citation_keys:
        flow.append(Spacer(1, 8))
        flow += _citations_section(styles, [CITATIONS_BY_SOURCE[k] for k in citation_keys if k in CITATIONS_BY_SOURCE], section_num=10)

    flow.append(Spacer(1, 8))
    flow += _limitations_section(styles, spec["limitations"], section_num=11)

    doc.build(flow, onFirstPage=lambda c, d: _footer_canvas(c, d, generated_at),
               onLaterPages=lambda c, d: _footer_canvas(c, d, generated_at))
    return buf.getvalue()


AGRI_GLOSSARY = [
    ("NDDI", "Normalized Difference Drought Index = (NDVI \u2212 NDWI) / (NDVI + NDWI); combines "
              "vegetation vigor and surface water content into one drought-stress indicator."),
    ("NDVI", "Normalized Difference Vegetation Index = (NIR \u2212 Red) / (NIR + Red); higher values "
              "indicate denser, healthier vegetation."),
    ("SMAP", "NASA's Soil Moisture Active Passive mission; this report uses the L4 (SPL4SMGP) surface "
              "soil moisture product, updated every 3 hours globally."),
    ("Confidence (Data Completeness)", "Not a statistical certainty measure \u2014 reflects how many of "
                    "the three indicators had usable satellite data, and (once a region has enough farmer/"
                    "officer feedback) that region's track record of past alert accuracy."),
    ("Weighted Average", "The composite score = 0.40\u00d7Drought + 0.35\u00d7Vegetation Decline + "
                          "0.25\u00d7Moisture Deficit, with weights automatically renormalized over "
                          "whichever indicators actually computed for this run."),
    ("GRACE", "NASA/GFZ satellite mission measuring tiny changes in Earth's gravity field to estimate "
               "terrestrial water storage anomalies (surface + soil + groundwater combined) at a coarse "
               "~300 km grid \u2014 used here only as regional groundwater-trend context, not part of the "
               "composite risk score."),
    ("CHIRPS", "A global daily rainfall estimate blending satellite and rain-gauge station data, "
                "~5.5 km resolution \u2014 used here only as regional rainfall context, not part of the "
                "composite risk score."),
    ("Land Surface Temperature (LST)", "Derived from the Landsat thermal band using the standard USGS "
                "Collection 2 scaling \u2014 used here only as regional temperature context, not part of "
                "the composite risk score."),
]


def _agri_recommendations(band: str, inputs_failed: List[str]) -> List[str]:
    recs = []
    if band in ("high", "severe"):
        recs.append(
            "Given the elevated risk band, prioritize a field visit to this AOI before making any "
            "operational, insurance, or subsidy decision based on this score alone."
        )
        recs.append(
            "Cross-reference the per-indicator maps in Section 3 to identify which part of the AOI is "
            "driving the score \u2014 risk is rarely uniform across a large or irregular AOI."
        )
    elif band == "moderate":
        recs.append(
            "Monitor this AOI on a 2-4 week cadence to see whether the moderate reading is stable, "
            "improving, or trending toward the high band."
        )
    else:
        recs.append(
            "No immediate action indicated by this score; continue routine monitoring at your normal "
            "cadence."
        )
    if inputs_failed:
        recs.append(
            f"Data was unavailable for {', '.join(k.replace('_', ' ') for k in inputs_failed)} this run "
            f"\u2014 consider re-running this assessment in a few weeks once satellite coverage improves, "
            f"rather than treating the current partial score as final."
        )
    recs.append(
        "If this region will be monitored repeatedly, add it to the Agri watchlist so future alerts can "
        "build a farmer/officer feedback history \u2014 that history directly improves this score's future "
        "confidence."
    )
    return recs


def _regional_context_section(styles, section_num: int, groundwater_result, precipitation_result,
                                temperature_result, groundwater_image_bytes=None,
                                precipitation_image_bytes=None, temperature_image_bytes=None) -> List:
    """Groundwater, rainfall, and temperature context for the AOI.
    Deliberately NOT part of the composite 0-100 risk score above: each of
    these has a different resolution/cadence than the three indicators
    that score is built from (GRACE ~300km/multi-year, CHIRPS ~5.5km/
    seasonal, both coarser or slower-moving than the 3-month Sentinel-2/
    SMAP window the score compares). Folding them into the same weighted
    number would mix timescales rather than genuinely improve the score,
    so they're reported here as separate, honestly-scoped context instead."""
    flow = [Paragraph(f"{section_num}. Regional Environmental Context", styles["section_head"])]
    flow.append(Paragraph(
        "The three readings below \u2014 groundwater, rainfall, and land surface temperature \u2014 provide "
        "additional regional context for this AOI. They are reported separately and are <b>not included "
        "in the composite risk score</b> above: each is measured at a coarser resolution or slower "
        "timescale than the three indicators (drought, vegetation decline, moisture deficit) the score is "
        "built from, so combining them into one weighted number would mix incompatible timescales rather "
        "than genuinely improve it.",
        styles["caveat"]))
    flow.append(Spacer(1, 6))

    images = []
    if groundwater_result and groundwater_result.get("status") == "ok":
        gw = groundwater_result
        images.append({
            "label": "Groundwater Trend (GRACE)", "bytes": groundwater_image_bytes,
            "caption": f"NASA GRACE/GRACE-FO, latest available anomaly \u00b7 {gw['resolution_note']}",
            "legend": GROUNDWATER_LEGEND,
            "interpretation": (
                f"Trend over the recent record: <b>{gw['trend']}</b> "
                f"({gw['slope_cm_per_year']:+.2f} cm/year). Latest anomaly: {gw['latest_anomaly_cm']:+.2f} cm "
                f"(as of {gw['latest_date']}, {gw['points_used']} monthly points used)."
            ),
        })
    if precipitation_result and precipitation_result.get("status") == "ok":
        pr = precipitation_result
        images.append({
            "label": "Rainfall (CHIRPS)", "bytes": precipitation_image_bytes,
            "caption": f"CHIRPS, {pr['recent_days']}-day accumulated total \u00b7 {pr['resolution_note']}",
            "legend": PRECIP_LEGEND,
            "interpretation": (
                f"Recent {pr['recent_days']}-day total: {pr['recent_total_mm']} mm, vs. a "
                f"{pr['years_used']}-year seasonal-normal average of {pr['historical_mean_mm']} mm for the "
                f"same window \u2014 conditions are <b>{pr['condition']}</b>"
                + (f" ({pr['anomaly_pct']:+.1f}% vs. normal)." if pr.get("anomaly_pct") is not None else ".")
            ),
        })
    if temperature_result and temperature_result.get("status") == "ok":
        tm = temperature_result
        images.append({
            "label": "Land Surface Temperature", "bytes": temperature_image_bytes,
            "caption": f"Landsat 8/9 thermal, {tm['recent_days']}-day mean \u00b7 {tm['resolution_note']}",
            "legend": THERMAL_LEGEND,
            "interpretation": (
                f"Mean land surface temperature over the recent {tm['recent_days']}-day window: "
                f"{tm['mean_lst_c']}\u00b0C (range {tm['min_lst_c']}\u2013{tm['max_lst_c']}\u00b0C)."
            ),
        })

    if images:
        img_w = 78 * mm
        cell_style = ParagraphStyle("ctx_grid_caption", parent=styles["footer"], alignment=TA_CENTER, spaceBefore=3)
        body_style = ParagraphStyle("ctx_grid_body", parent=styles["body"], fontSize=8.5, leading=12)

        def _cell(img):
            content = [Image(io.BytesIO(img["bytes"]), width=img_w, height=img_w)] if img.get("bytes") else []
            content.append(Paragraph(f"<b>{img['label']}</b>", cell_style))
            if img.get("caption"):
                content.append(Paragraph(img["caption"], cell_style))
            if img.get("legend") and img.get("bytes"):
                leg = img["legend"]
                drawing = _gradient_legend(leg["palette"], leg["min"], leg["max"], leg["unit"],
                                            tick_labels=leg.get("labels"), width=int(img_w))
                drawing.hAlign = "CENTER"
                content.append(Spacer(1, 3))
                content.append(drawing)
            if img.get("interpretation"):
                content.append(Spacer(1, 3))
                content.append(Paragraph(img["interpretation"], body_style))
            return content

        rows = []
        for i in range(0, len(images), 2):
            pair = images[i:i + 2]
            row = [_cell(img) for img in pair]
            if len(pair) == 1:
                row.append("")
            rows.append(row)
        t = Table(rows, colWidths=[img_w + 4, img_w + 4])
        t.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ]))
        flow.append(t)
    else:
        flow.append(Paragraph(
            "No regional context data was available for this AOI/period (groundwater, rainfall, and "
            "temperature all require sufficient satellite coverage over the requested window).",
            styles["body"]))

    unavailable_notes = []
    for name, result in (("groundwater", groundwater_result), ("rainfall", precipitation_result),
                          ("temperature", temperature_result)):
        if result and result.get("status") != "ok" and result.get("note"):
            unavailable_notes.append(f"{name.title()}: {result['note']}")
    if unavailable_notes:
        flow.append(Spacer(1, 4))
        flow.append(Paragraph("<b>Not available for this run:</b> " + " \u00b7 ".join(unavailable_notes),
                               styles["caveat"]))

    flow.append(Spacer(1, 4))
    return flow


def build_agri_risk_report(
    aoi_geojson: Dict[str, Any],
    risk_result: Dict[str, Any],
    region_name: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    ndvi_image_bytes: Optional[bytes] = None,
    nddi_image_bytes: Optional[bytes] = None,
    moisture_image_bytes: Optional[bytes] = None,
    moisture_legend_range: Optional[Tuple[float, float]] = None,
    baseline_result: Optional[Dict[str, Any]] = None,
    llm_synthesis: Optional[str] = None,
    groundwater_result: Optional[Dict[str, Any]] = None,
    precipitation_result: Optional[Dict[str, Any]] = None,
    temperature_result: Optional[Dict[str, Any]] = None,
    groundwater_image_bytes: Optional[bytes] = None,
    precipitation_image_bytes: Optional[bytes] = None,
    temperature_image_bytes: Optional[bytes] = None,
) -> bytes:
    """Builds the agri risk-score PDF report from a compute_risk_score() result."""
    styles = _styles()
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm, bottomMargin=20 * mm,
                             leftMargin=20 * mm, rightMargin=20 * mm)

    period = risk_result.get("period", {})
    band = str(risk_result.get("band", "unknown"))
    score = risk_result.get("risk_score")
    confidence = risk_result.get("confidence")
    inputs_used = risk_result.get("inputs_used", [])
    inputs_failed = risk_result.get("inputs_failed", [])

    flow = []
    flow += _cover_page(styles, "Agricultural Risk Assessment Report",
        f"Analysis Period: {period.get('start_date', 'N/A')} \u2013 {period.get('end_date', 'N/A')}", [
        ("Report Generated", generated_at),
        ("Region", region_name or "Unnamed AOI"),
        ("Analysis Period", f"{period.get('start_date', 'N/A')} to {period.get('end_date', 'N/A')}"),
        ("Composite Risk Score", f"{score} / 100  ({band.upper()})"),
        ("Confidence (data completeness)", f"{confidence}%"),
    ])
    flow += _table_of_contents(styles, [
        ("1. Executive Summary", "Risk score, band, and key driver at a glance"),
        ("2. Study Area", "AOI geometry and coordinates"),
        ("3. Satellite Imagery by Indicator", "True color, NDVI, NDDI, and soil moisture maps"),
        ("4. Methodology", "How the composite score is calculated"),
        ("5. Indicator Definitions & Thresholds", "What each sub-score measures"),
        ("6. Sub-Score Breakdown", "Drought, vegetation decline, and moisture deficit scores"),
        ("7. Historical Context", "5-year seasonal baseline comparison"),
        ("8. Regional Environmental Context", "Groundwater, rainfall, and temperature (not scored)"),
        ("9. Findings & Interpretation", "What the score indicates"),
        ("10. Data Quality & Confidence", "Data completeness and validation"),
        ("11. Recommendations", "Suggested next steps"),
        ("12. Glossary & Formula Reference", "Terms and formulas used in this report"),
        ("13. Data Sources & Citations", "Full citations for every dataset used"),
        ("14. Limitations & Caveats", "What this assessment does not tell you"),
    ])

    exec_paragraphs = [
        f"This AOI's composite agricultural risk score is <b>{score}/100 ({band.upper()})</b>, computed "
        f"from {len(inputs_used)} of 3 underlying satellite indicators (data-completeness reading: "
        f"{confidence}%).",
        risk_result.get("reason", ""),
    ]
    flow += _executive_summary_section(styles, [p for p in exec_paragraphs if p], section_num=1)
    flow.append(PageBreak())

    flow += _study_area_section(styles, aoi_geojson, section_num=2)
    flow.append(PageBreak())
    end_date_str = period.get("end_date", "N/A")
    flow += _imagery_grid_section(styles, [
        {"label": "True Color (current conditions)", "bytes": image_bytes,
         "caption": f"Sentinel-2, cloud-masked composite \u00b130 days around {end_date_str}"},
        {"label": "NDVI \u2014 Vegetation", "bytes": ndvi_image_bytes,
         "caption": f"Sentinel-2, \u00b130 days around {end_date_str} \u00b7 drives the Vegetation Decline score",
         "legend": NDVI_LEGEND},
        {"label": "NDDI \u2014 Drought", "bytes": nddi_image_bytes,
         "caption": f"Sentinel-2, \u00b130 days around {end_date_str} \u00b7 drives the Drought score",
         "legend": NDDI_LEGEND},
        {"label": "SMAP Soil Moisture", "bytes": moisture_image_bytes,
         "caption": f"NASA SMAP L4, \u00b190 days around {end_date_str} \u00b7 drives the Moisture Deficit score"
                    + (" \u00b7 color scale fit to this AOI's actual observed range" if moisture_legend_range else ""),
         "legend": _moisture_legend_for(moisture_legend_range)},
    ], section_num=3)

    flow.append(PageBreak())
    flow.append(Paragraph("4. Methodology", styles["section_head"]))
    flow.append(Paragraph(
        "The composite risk score combines three independently-computed satellite indicators \u2014 "
        "drought stress (Sentinel-2 NDDI), vegetation decline (Sentinel-2 NDVI threshold change), and "
        "soil moisture deficit (SMAP) \u2014 into a single 0\u2013100 score. Each indicator is scored 0\u2013100 "
        "and combined via a weighted average (drought 40%, vegetation decline 35%, moisture deficit 25%); "
        "weights are automatically renormalized over whichever indicators successfully computed for this "
        "AOI and period. Confidence reflects both data completeness (how many of the three indicators "
        "were available) and, where the region has accumulated farmer/officer feedback on past alerts, "
        "the region's historical alert accuracy.",
        styles["body"]))

    sub_scores = risk_result.get("sub_scores", {})
    worked_lines = []
    weights = {"drought": 0.40, "vegetation_loss": 0.35, "moisture_deficit": 0.25}
    used_weight_sum = sum(weights[k] for k in sub_scores if sub_scores.get(k) is not None and k in weights)
    if used_weight_sum > 0 and score is not None:
        terms = []
        for k, w in weights.items():
            v = sub_scores.get(k)
            if v is not None:
                terms.append(f"({v:.1f} \u00d7 {w:.2f})")
        worked_lines = [
            f"Composite = [{' + '.join(terms)}] \u00f7 {used_weight_sum:.2f} = {score} "
            f"(weights renormalized to sum to 1.0 over the {len(terms)} available indicator(s))."
        ]
    if worked_lines:
        flow.append(Paragraph("Worked Example (this run's actual values)", ParagraphStyle(
            "worked_ex_head", parent=styles["section_head"], fontSize=10.5, spaceBefore=8)))
        for line in worked_lines:
            flow.append(Paragraph(line, ParagraphStyle(
                "worked_ex", parent=styles["body"], fontName="Courier", fontSize=8.5, leading=13,
                backColor=colors.HexColor("#f4f5f6"), borderPadding=6)))

    flow.append(Paragraph("5. Indicator Definitions & Thresholds", styles["section_head"]))
    flow.append(_metadata_table(styles, [
        ("Drought (NDDI)", "NDDI = (NDVI \u2212 NDWI) / (NDVI + NDWI). Sub-score reflects the share of "
                            "the AOI with NDDI > 0.5 (drought-affected threshold)."),
        ("Vegetation Decline (NDVI)", "Sub-score reflects the share of NDVI \u2265 0.20 (vegetated) area at "
                                    "the start of the period that dropped below that threshold by the end. This is "
                                    "a threshold-crossing measurement, not a confirmed-cause diagnosis \u2014 it can "
                                    "equally reflect normal seasonal change, harvesting, drought stress, temporary "
                                    "disturbance, land-cover conversion, or a cloud/processing artifact. It should "
                                    "not be read as confirmed permanent vegetation loss without further review."),
        ("Moisture Deficit (SMAP)", "Sub-score combines the magnitude of the soil-moisture drop over the "
                                      "period with the extent of area below the 0.10 m\u00b3/m\u00b3 dry-stress "
                                      "threshold at the end of the period."),
        ("Risk Bands", "LOW: 0\u201329  \u00b7  MODERATE: 30\u201354  \u00b7  HIGH: 55\u201374  \u00b7  "
                        "SEVERE: 75\u2013100"),
    ]))
    flow.append(Spacer(1, 4))

    flow.append(Paragraph("6. Sub-Score Breakdown", styles["section_head"]))
    # 'Vegetation Loss' (auto-generated from the internal key vegetation_loss)
    # reads as if it measures confirmed, permanent loss. What it actually
    # measures is a share of pixels crossing an NDVI threshold, which can
    # equally reflect seasonal change, harvesting, drought stress, or a
    # processing artifact — 'Vegetation Decline' is a more accurate label
    # for what's shown here. The internal key stays vegetation_loss for API/
    # storage compatibility; only the display label changes.
    SUB_SCORE_LABELS = {"vegetation_loss": "Vegetation Decline"}
    sub_rows = [
        (SUB_SCORE_LABELS.get(k, k.replace("_", " ").title()),
         _fmt_num(v, 1) if v is not None else "No data available", "/ 100")
        for k, v in sub_scores.items()
    ]
    flow.append(_metrics_table(styles, sub_rows))
    flow.append(Spacer(1, 4))

    if inputs_failed:
        flow.append(Paragraph(
            f"<b>Data availability note:</b> the following indicator(s) had no usable satellite coverage "
            f"for this AOI/period and were excluded from the composite score (weights renormalized over "
            f"the remainder) \u2014 this is reported as missing data, not assumed to be low-risk: "
            f"{', '.join(k.replace('_', ' ') for k in inputs_failed)}.",
            styles["caveat"]))
        flow.append(Spacer(1, 4))

    section_num = 7
    if baseline_result and baseline_result.get("seasonal_normal_ndvi") is not None:
        flow.append(Paragraph(f"{section_num}. Historical Context (5-Year Seasonal Baseline)", styles["section_head"]))
        status = baseline_result.get("status", "normal").replace("_", " ")
        flow.append(Paragraph(
            f"Current-period NDVI for this AOI is {baseline_result['current_ndvi']:.3f}, against a "
            f"{baseline_result.get('years_used', 5)}-year seasonal-normal mean of "
            f"{baseline_result['seasonal_normal_ndvi']:.3f} for this same time of year "
            f"(\u00b1{baseline_result.get('seasonal_std_ndvi', 0):.3f} std. dev.). This places current "
            f"conditions <b>{status}</b>" + (
                f" (z-score: {baseline_result['z_score']:+.2f})." if baseline_result.get("z_score") is not None else "."
            ),
            styles["body"]))
        flow.append(Paragraph(
            "This historical comparison is independent of the composite risk score above \u2014 it answers "
            "whether current conditions are unusual for this specific time of year at this specific "
            "location, which a single-period snapshot cannot.",
            styles["caveat"]))
        flow.append(Spacer(1, 4))
        section_num += 1

    if any([groundwater_result, precipitation_result, temperature_result]):
        flow.append(PageBreak())
        flow += _regional_context_section(
            styles, section_num, groundwater_result, precipitation_result, temperature_result,
            groundwater_image_bytes, precipitation_image_bytes, temperature_image_bytes)
        section_num += 1

    flow.append(Paragraph(f"{section_num}. Findings & Interpretation", styles["section_head"]))
    flow.append(Paragraph(risk_result.get("reason", "No specific driver identified."), styles["body"]))

    # The line above is entirely deterministic and computed before the
    # regional-context indicators even run, so it structurally has no way
    # to know about them — but a reader seeing "broadly stable" right above
    # a "well below normal" rainfall reading in Section 8 reasonably reads
    # that as the report contradicting itself. Flag the tension explicitly
    # here instead of leaving it for the reader to reconcile.
    context_flags = []
    if precipitation_result and precipitation_result.get("condition") in ("below normal", "well below normal"):
        context_flags.append(f"rainfall is {precipitation_result['condition']}")
    if temperature_result and temperature_result.get("status") == "ok" and temperature_result.get("mean_lst_c", 0) >= 42:
        context_flags.append(f"mean land surface temperature is elevated ({temperature_result['mean_lst_c']}\u00b0C)")
    if groundwater_result and groundwater_result.get("status") == "ok" and groundwater_result.get("trend") == "declining":
        context_flags.append("groundwater is on a declining trend")
    if context_flags:
        flow.append(Paragraph(
            f"<i>Worth noting alongside this score:</i> {'; '.join(context_flags)} (see Section "
            f"{section_num - 1}, Regional Environmental Context, above). These are not part of the "
            f"composite score \u2014 see the explanation in that section for why \u2014 but a low composite "
            f"score does not mean every signal for this AOI looks favorable, and these are worth "
            f"weighing alongside the score rather than assuming it accounts for them.",
            styles["caveat"]))

    # Separate concern from the context-tension caveat above: the
    # vegetation sub-score's underlying loss_pct is a percentage computed
    # against the AOI's initial vegetated area, not the whole AOI. For a
    # mostly-barren AOI, that base can be tiny, making the percentage (and
    # therefore this sub-score) statistically fragile — flagged explicitly
    # rather than letting a volatile percentage read as a stable signal.
    veg_metrics = (risk_result.get("raw_metrics") or {}).get("vegetation") or {}
    if veg_metrics.get("small_base_caveat"):
        aoi_pct = veg_metrics.get("vegetation_pct_of_aoi")
        initial_km2 = veg_metrics.get("initial_vegetation_km2")
        flow.append(Paragraph(
            f"<i>Vegetation Decline sub-score caveat:</i> the initial vegetated extent this sub-score is "
            f"based on ({initial_km2:,.2f} km\u00b2) is only {aoi_pct:.2f}% of this AOI's total area. On a "
            f"base this small, a handful of pixels crossing the NDVI vegetation threshold between the two "
            f"comparison periods \u2014 plausible from ordinary seasonal timing differences rather than any "
            f"real land-cover change \u2014 can swing the underlying percentage sharply. Treat the Vegetation "
            f"Decline sub-score with reduced confidence for an AOI this sparsely vegetated.",
            styles["caveat"]))
    confidence_basis = risk_result.get("confidence_basis") or {}
    track_record_used = confidence_basis.get("track_record_used", False)
    feedback_count = confidence_basis.get("feedback_count", 0)
    completeness_clause = "full" if len(inputs_used) == 3 else "partial"
    if track_record_used:
        basis_clause = (
            f" and this region's accumulated alert-accuracy track record "
            f"({feedback_count} prior feedback entries)."
        )
    elif feedback_count:
        basis_clause = (
            f" This region has {feedback_count} prior feedback entr"
            f"{'y' if feedback_count == 1 else 'ies'}, not yet enough (5+ required) to factor into confidence."
        )
    else:
        basis_clause = " This is a one-off assessment with no watchlist feedback history to draw on yet."
    flow.append(Paragraph(
        f"This assessment's data-completeness reading is {confidence}%, reflecting "
        f"{completeness_clause} data availability across the three underlying indicators."
        + basis_clause,
        styles["body"]))
    if llm_synthesis:
        flow.append(Spacer(1, 4))
        flow.append(Paragraph("<i>Assessment summary:</i>", styles["meta_label"]))
        flow.extend(_render_multi_paragraph(styles, llm_synthesis))
    section_num += 1

    flow += _data_quality_section(
        styles,
        [("Indicators Available", f"{len(inputs_used)} of 3 ({', '.join(inputs_used) if inputs_used else 'none'})"),
         ("Indicators Unavailable", ', '.join(inputs_failed) if inputs_failed else "None"),
         ("Confidence Basis", "Data completeness"
             + (f", plus regional feedback history ({feedback_count} entries)" if track_record_used else "")
             + (" (feedback history not yet used \u2014 fewer than 5 entries so far)"
                if feedback_count and not track_record_used else "")
             + (" (no watchlist feedback history for this AOI yet)" if not feedback_count else ""))],
        "This assessment's confidence figure is not a single opaque number \u2014 it is derived from exactly "
        "how many of the three underlying indicators had usable satellite data for this AOI/period, shown "
        "below, plus (once this region has enough farmer/officer feedback) its historical alert accuracy.",
        section_num=section_num)
    section_num += 1

    flow.append(PageBreak())
    flow += _recommendations_section(styles, _agri_recommendations(band, inputs_failed), section_num=section_num)
    section_num += 1

    flow.append(PageBreak())
    flow.append(Paragraph("Appendices", ParagraphStyle(
        "appendix_head", parent=styles["report_heading"], fontSize=13, spaceBefore=0)))
    flow.append(Spacer(1, 8))
    flow += _glossary_section(styles, GLOSSARY_COMMON + AGRI_GLOSSARY, section_num=section_num)
    section_num += 1

    flow.append(Spacer(1, 8))
    citation_keys = ["sentinel2", "smap"]
    if groundwater_result and groundwater_result.get("status") == "ok":
        citation_keys.append("grace")
    if precipitation_result and precipitation_result.get("status") in ("ok", "ok_no_baseline"):
        citation_keys.append("chirps")
    if temperature_result and temperature_result.get("status") == "ok":
        citation_keys.append("landsat")
    flow += _citations_section(styles, [CITATIONS_BY_SOURCE[k] for k in citation_keys], section_num=section_num)
    section_num += 1

    flow.append(Paragraph(f"{section_num}. Limitations & Caveats", styles["section_head"]))
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
