"""
root_zone.py - modelled soil moisture BELOW the 0-5 cm that SMAP measures directly. GEE paths are untested live from the dev sandbox (mock-tested only).

What is actually available (checked against the Earth Engine catalog):
  * SMAP measures only the top ~5 cm (L-band). NASA/SMAP/SPL4SMGP/008 is the L4 product: SMAP brightness temperature assimilated into a land-surface
    model, which ALSO outputs `sm_rootzone` (0-100 cm), `sm_profile` (to model bedrock) and `sm_rootzone_pctl` (root-zone percentile). ~9-11 km, 3-hourly.
    These deeper values are MODEL output, not measurements. There is no 15-25 cm layer.
  * ECMWF/ERA5_LAND/DAILY_AGGR (reanalysis model, ~11 km): volumetric_soil_water_layer_1..4 = 0-7, 7-28, 28-100, 100-289 cm. Layer 2 (7-28 cm) is the nearest
    published layer to 15-25 cm and spans it.
Both are regional, modelled reads; field sensors are what would calibrate anything finer.
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)
DEPTH_NOTE = ("SMAP itself senses ~0-5 cm. Deeper values here are model output: SMAP L4 root zone (0-100 cm, SMAP data assimilated into a land-surface model) and "
              "ERA5-Land layers (reanalysis). No 15-25 cm layer exists; ERA5-Land layer 2 (7-28 cm) spans it. Regional (~10 km) estimates, not field measurements.")
RZ_DRY_PCTL = 20.0             # percentile at/below which root-zone moisture counts as dry (US Drought Monitor D1 convention; a heuristic, not an ICAR threshold)
RZ_BELOW_NORMAL_PCTL = 30.0    # below-normal band (US Drought Monitor D0 convention)


def _region_mean(img, region):
    return img.reduceRegion(reducer=__import__("ee").Reducer.mean(), geometry=region, scale=10000, maxPixels=1e9, bestEffort=True, tileScale=4).getInfo() or {}


def fetch_root_zone(aoi: Dict[str, Any], as_of: Optional[str] = None) -> Dict[str, Any]:
    """Area-mean SMAP L4 (surface, root zone, root-zone percentile) over the last 14 days, and ERA5-Land layers 2-3 over the last 30 days
    (ERA5-Land in Earth Engine lags real time by days). Each source fails independently; a missing source is None, never an exception."""
    from .. import gee_client as g
    ee = g.ee
    end = as_of or datetime.utcnow().strftime("%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    region, end_ee = g._polygon_geometry(aoi), g._cap_end_date(end)
    out: Dict[str, Any] = {"window_end": end, "smap": None, "era5": None, "errors": []}
    try:
        col = ee.ImageCollection("NASA/SMAP/SPL4SMGP/008").filterBounds(region).filterDate(end_ee.advance(-14, "day"), end_ee).select(["sm_surface", "sm_rootzone", "sm_rootzone_pctl"])
        if col.size().getInfo() > 0:
            m = _region_mean(col.mean(), region)
            out["smap"] = {"sm_surface": m.get("sm_surface"), "sm_rootzone": m.get("sm_rootzone"), "sm_rootzone_pctl": m.get("sm_rootzone_pctl"), "window_days": 14}
    except Exception as e:
        logger.warning(f"root zone: SMAP L4 failed: {e}"); out["errors"].append(f"smap: {e}")
    try:
        col = ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR").filterBounds(region).filterDate(end_ee.advance(-30, "day"), end_ee).select(["volumetric_soil_water_layer_2", "volumetric_soil_water_layer_3"])
        if col.size().getInfo() > 0:
            m = _region_mean(col.mean(), region)
            out["era5"] = {"layer2_7_28cm": m.get("volumetric_soil_water_layer_2"), "layer3_28_100cm": m.get("volumetric_soil_water_layer_3"), "window_days": 30}
    except Exception as e:
        logger.warning(f"root zone: ERA5-Land failed: {e}"); out["errors"].append(f"era5: {e}")
    return out


def classify_root_zone(pctl: Optional[float]) -> str:
    """'dry' / 'below_normal' / 'adequate' / 'unknown' from the SMAP L4 root-zone percentile."""
    if pctl is None: return "unknown"
    return "dry" if pctl <= RZ_DRY_PCTL else ("below_normal" if pctl <= RZ_BELOW_NORMAL_PCTL else "adequate")


def _r(v, nd=3): return None if v is None else round(float(v), nd)


def summarize_root_zone(raw: Dict[str, Any]) -> Dict[str, Any]:
    smap, era5 = raw.get("smap") or {}, raw.get("era5") or {}
    pctl = smap.get("sm_rootzone_pctl")
    return {"available": pctl is not None, "status": classify_root_zone(pctl), "sm_surface_0_5cm": _r(smap.get("sm_surface")), "sm_rootzone_0_100cm": _r(smap.get("sm_rootzone")),
            "sm_rootzone_percentile": None if pctl is None else round(float(pctl), 1), "era5_layer2_7_28cm": _r(era5.get("layer2_7_28cm")),
            "era5_layer3_28_100cm": _r(era5.get("layer3_28_100cm")), "source_errors": raw.get("errors") or [], "depth_note": DEPTH_NOTE,
            "thresholds": {"dry_pctl_at_or_below": RZ_DRY_PCTL, "below_normal_pctl_at_or_below": RZ_BELOW_NORMAL_PCTL, "basis": "US Drought Monitor percentile convention (heuristic)"}}
