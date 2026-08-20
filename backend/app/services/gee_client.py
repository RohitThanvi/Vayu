import logging
import datetime
import os
import json
import tempfile
from typing import Dict, Any

import ee

logger = logging.getLogger(__name__)

# ── GEE Initialization ────────────────────────────────────────────────────────
# Non-fatal by design: if GEE fails to initialize (bad/missing credentials),
# we log it clearly but let the app keep running. The intel feed and vessel
# tracking features don't depend on GEE, so a broken GEE credential
# shouldn't take down the whole container — only satellite analysis queries
# will fail (with a clear error) until the credential is fixed.
GEE_READY = False
GEE_INIT_ERROR = None


def _initialize_gee():
    global GEE_READY, GEE_INIT_ERROR
    creds_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if creds_json:
        # Server/Docker/Render deployment — use service account from env var
        try:
            creds_dict = json.loads(creds_json)
            credentials = ee.ServiceAccountCredentials(
                email=creds_dict["client_email"],
                key_data=creds_json,
            )
            ee.Initialize(credentials)
            logger.info("GEE initialized with service account.")
            GEE_READY = True
            return
        except Exception as e:
            GEE_INIT_ERROR = str(e)
            logger.error(
                f"GEE service account init failed: {e}. Satellite analysis "
                f"will be unavailable until GOOGLE_APPLICATION_CREDENTIALS_JSON "
                f"is fixed. Other features (intel feed, vessels) are unaffected."
            )
            return

    # Local development — use earthengine authenticate credentials
    try:
        project = os.environ.get("GCP_PROJECT_ID", "")
        if project:
            ee.Initialize(project=project)
        else:
            try:
                ee.Initialize()
            except ee.EEException:
                raise ee.EEException(
                    "No GCP_PROJECT_ID set in .env and no default project configured. "
                    "Run: earthengine set_project YOUR_PROJECT_ID"
                )
        logger.info("GEE initialized with local credentials.")
        GEE_READY = True
    except Exception as e:
        GEE_INIT_ERROR = str(e)
        logger.error(
            f"GEE init error: {e}. Run 'earthengine authenticate' locally, or set "
            f"GOOGLE_APPLICATION_CREDENTIALS_JSON for server/Docker deployments. "
            f"Satellite analysis will be unavailable until this is fixed."
        )

_initialize_gee()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _today_ee() -> ee.Date:
    return ee.Date(datetime.datetime.utcnow().strftime("%Y-%m-%d"))


def _cap_end_date(end_date: str) -> ee.Date:
    today = _today_ee()
    end = ee.Date(end_date)
    return ee.Date(ee.Algorithms.If(end.difference(today, "day").gt(0), today, end))


def _polygon_geometry(aoi: Dict[str, Any]) -> ee.Geometry:
    """Accept Polygon, MultiPolygon, Feature, or FeatureCollection."""
    geo_type = aoi.get("type")
    if geo_type == "Feature":
        aoi = aoi["geometry"]
        geo_type = aoi.get("type")
    if geo_type == "FeatureCollection":
        aoi = aoi["features"][0]["geometry"]
        geo_type = aoi.get("type")
    if geo_type == "Polygon":
        return ee.Geometry.Polygon(aoi["coordinates"])
    if geo_type == "MultiPolygon":
        return ee.Geometry.MultiPolygon(aoi["coordinates"])
    raise ValueError(f"Unsupported geometry type: {geo_type}")


def _require_start_after(start_date: str, cutoff: str, dataset_name: str):
    start = ee.Date(start_date)
    cutoff_ee = ee.Date(cutoff)
    if start.difference(cutoff_ee, "day").lt(0).getInfo():
        raise ValueError(
            f"{dataset_name} data is only available from {cutoff}. "
            f"Please choose a start date on or after {cutoff}."
        )


def _mask_s2_clouds(image: ee.Image) -> ee.Image:
    # QA60 stopped being reliably populated for Sentinel-2 scenes processed
    # under baseline 04.00+ (after 2022-01-25) until Feb 2024, and even the
    # "legacy-reconstructed" QA60 post-2024 is less reliable than SCL. Use the
    # Scene Classification Layer instead: mask out cloud shadow (3), cloud
    # medium/high probability (8, 9), and cirrus (10).
    scl = image.select("SCL")
    mask = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10))
    return image.updateMask(mask).divide(10000)


def _sentinel2_ndvi_composite(region: ee.Geometry, start: ee.Date, end: ee.Date) -> ee.Image:
    col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(region)
        .filterDate(start, end)
        .map(_mask_s2_clouds)
    )
    if col.size().getInfo() == 0:
        raise ValueError(f"No cloud-free Sentinel-2 imagery found for {start.format('YYYY-MM-dd').getInfo()} – {end.format('YYYY-MM-dd').getInfo()}.")
    composite = col.median()
    required = ee.List(["B4", "B8"])
    if not composite.bandNames().containsAll(required).getInfo():
        raise ValueError("Composite is missing required bands B4/B8. Try a wider date range.")
    return composite.normalizedDifference(["B8", "B4"]).rename("NDVI")


# ── Area helpers ──────────────────────────────────────────────────────────────

def _calc_area_km2(mask: ee.Image, region: ee.Geometry, scale: int = 100) -> float:
    """Calculate area in km². Uses scale=100m by default to avoid memory limits.
    tileScale=4 splits the reduction into smaller tiles server-side — this is
    the fix GEE recommends for "User memory limit exceeded" (bestEffort alone
    adjusts the pixel scale, but large/complex AOIs like a whole district
    boundary from the place-search bar can still blow the interactive compute
    budget on the reduction step itself)."""
    result = (
        ee.Image.pixelArea()
        .updateMask(mask)
        .reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=region,
            scale=scale,
            maxPixels=1e9,
            bestEffort=True,   # auto-increases scale if needed to avoid memory errors
            tileScale=4,       # splits computation into smaller tiles to reduce peak memory
        )
        .get("area")
        .getInfo()
    ) or 0
    return result / 1_000_000


def _region_area_km2(region: ee.Geometry) -> float:
    """Total AOI area in km², independent of any mask. Used to convert an
    absolute affected-area reading (km²) into a share of the AOI — an
    absolute km² figure alone means very different things for a 20 km² AOI
    vs. a 2,000 km² one, so any 'severity' score derived from area must be
    normalized against this, not used as a raw capped number."""
    return (region.area(maxError=1).divide(1_000_000).getInfo()) or 0


# ═════════════════════════════════════════════════════════════════════════════
# METRIC FUNCTIONS
# ═════════════════════════════════════════════════════════════════════════════

def compute_vegetation_change(aoi: Dict, start_date: str, end_date: str) -> Dict:
    logger.info(f"GEE: vegetation_change {start_date} → {end_date}")
    _require_start_after(start_date, "2015-06-23", "Sentinel-2")
    end_ee = _cap_end_date(end_date)
    region = _polygon_geometry(aoi)
    start_ee = ee.Date(start_date)

    start_ndvi = _sentinel2_ndvi_composite(region, start_ee, start_ee.advance(1, "year"))
    end_ndvi = _sentinel2_ndvi_composite(region, end_ee.advance(-1, "year"), end_ee)

    threshold = 0.2
    start_veg = start_ndvi.gte(threshold).unmask(0)
    end_veg = end_ndvi.gte(threshold).unmask(0)
    loss_mask = start_veg.And(end_veg.Not())
    gain_mask = end_veg.And(start_veg.Not())

    loss_km2 = _calc_area_km2(loss_mask, region)
    gain_km2 = _calc_area_km2(gain_mask, region)
    initial_km2 = _calc_area_km2(start_veg, region)
    loss_pct = (loss_km2 / initial_km2 * 100) if initial_km2 > 0 else 0

    logger.info(f"GEE: vegetation loss={loss_km2:.2f} km², gain={gain_km2:.2f} km²")
    return {
        "metrics": {
            "vegetation_loss_km2": round(loss_km2, 4),
            "vegetation_gain_km2": round(gain_km2, 4),
            "initial_vegetation_km2": round(initial_km2, 4),
            "net_change_km2": round(gain_km2 - loss_km2, 4),
            "loss_pct": round(loss_pct, 4),
        },
        "ee_image": loss_mask,
        "ee_geometry": region,
    }


def compute_builtup_change(aoi: Dict, start_date: str, end_date: str) -> Dict:
    logger.info(f"GEE: builtup_change {start_date} → {end_date}")
    _require_start_after(start_date, "2015-06-27", "Dynamic World")
    end_ee = _cap_end_date(end_date)
    region = _polygon_geometry(aoi)
    start_ee = ee.Date(start_date)
    BUILT_UP = 6

    dw = ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1").filterBounds(region)

    start_img = dw.filterDate(start_ee, start_ee.advance(1, "year")).mode()
    end_img = dw.filterDate(end_ee.advance(-1, "year"), end_ee).mode()

    start_mask = start_img.select("label").eq(BUILT_UP)
    end_mask = end_img.select("label").eq(BUILT_UP)
    gain_mask = end_mask.And(start_mask.Not())
    loss_mask = start_mask.And(end_mask.Not())

    gain_km2 = _calc_area_km2(gain_mask, region)
    loss_km2 = _calc_area_km2(loss_mask, region)
    initial_km2 = _calc_area_km2(start_mask, region)
    gain_pct = (gain_km2 / initial_km2 * 100) if initial_km2 > 0 else 0
    final_km2 = initial_km2 + gain_km2 - loss_km2
    region_area_km2 = _region_area_km2(region)
    # % of the whole AOI, not just % of the initial built-up area — for a
    # large regional AOI (a district/tehsil boundary rather than a compact
    # urban footprint), "+574 km²" reads very differently once you know
    # whether that's 3% of the AOI or 30% of it.
    initial_pct_of_aoi = round(initial_km2 / region_area_km2 * 100, 4) if region_area_km2 > 0 else None
    final_pct_of_aoi = round(final_km2 / region_area_km2 * 100, 4) if region_area_km2 > 0 else None

    logger.info(f"GEE: builtup gain={gain_km2:.2f} km²")
    return {
        "metrics": {
            "builtup_gain_km2": round(gain_km2, 4),
            "builtup_loss_km2": round(loss_km2, 4),
            "initial_builtup_km2": round(initial_km2, 4),
            "final_builtup_km2": round(final_km2, 4),
            "net_change_km2": round(gain_km2 - loss_km2, 4),
            "gain_pct": round(gain_pct, 4),
            "region_area_km2": round(region_area_km2, 4),
            "initial_builtup_pct_of_aoi": initial_pct_of_aoi,
            "final_builtup_pct_of_aoi": final_pct_of_aoi,
        },
        "ee_image": gain_mask,
        "ee_geometry": region,
    }


def compute_water_change(aoi: Dict, start_date: str, end_date: str) -> Dict:
    logger.info(f"GEE: water_change {start_date} → {end_date}")
    _require_start_after(start_date, "1984-01-01", "JRC Water")
    end_ee = _cap_end_date(end_date)
    region = _polygon_geometry(aoi)

    jrc = ee.ImageCollection("JRC/GSW1_4/MonthlyHistory").filterBounds(region)

    start_ee = ee.Date(start_date)
    start_water = jrc.filterDate(start_ee, start_ee.advance(1, "year")).mode().eq(2)
    end_water = jrc.filterDate(end_ee.advance(-1, "year"), end_ee).mode().eq(2)

    gain_mask = end_water.And(start_water.Not())
    loss_mask = start_water.And(end_water.Not())

    gain_km2 = _calc_area_km2(gain_mask, region)
    loss_km2 = _calc_area_km2(loss_mask, region)
    initial_km2 = _calc_area_km2(start_water, region)

    return {
        "metrics": {
            "water_gain_km2": round(gain_km2, 4),
            "water_loss_km2": round(loss_km2, 4),
            "initial_water_km2": round(initial_km2, 4),
            "net_change_km2": round(gain_km2 - loss_km2, 4),
        },
        "ee_image": gain_mask,
        "ee_geometry": region,
    }


def compute_flood_detection(aoi: Dict, start_date: str, end_date: str) -> Dict:
    """
    SAR-based flood detection using Sentinel-1 VV backscatter, following the
    standard UN-SPIDER Sentinel-1 flood-mapping recipe:

      1. Pre-flood REFERENCE composite: the most recent available scenes in
         the 30 days immediately before start_date (not a 3-month mean --
         averaging that far back risked diluting the actual flood signal
         and had confusing semantics relative to what the person asked for:
         "did X flood between date A and date B" should compare a reference
         just before A against the [A, B] window itself, not two arbitrary
         3-month means).
      2. FLOOD-PERIOD composite: scenes within [start_date, end_date] itself.
      3. Speckle filtering (focal median) on both composites before
         thresholding -- raw SAR backscatter is noisy pixel-to-pixel; without
         this, a meaningful fraction of the "flood" mask is just speckle
         noise, not real change.
      4. Backscatter drop >3dB flags a pixel as *possible* new water.
      5. Permanent water excluded via JRC Global Surface Water occurrence --
         a river or lake that's simply water in both periods isn't new
         flooding. Only previously-dry land that's now wet counts.
      6. Steep terrain (>5 deg slope, SRTM) excluded -- radar shadow/layover
         on slopes produces backscatter drops that look like flooding but
         are a geometry artifact, not water.

    Scene counts for both composites are surfaced in the metrics so the
    result is auditable -- if either composite is a single noisy scene, the
    person calling this should be able to see that rather than trust an
    opaque number.
    """
    logger.info(f"GEE: flood_detection {start_date} → {end_date}")
    _require_start_after(start_date, "2014-04-01", "Sentinel-1")
    end_ee = _cap_end_date(end_date)
    region = _polygon_geometry(aoi)
    start_ee = ee.Date(start_date)

    s1 = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(region)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .select("VV")
    )

    ref_start = start_ee.advance(-30, "day")
    ref_col = s1.filterDate(ref_start, start_ee)
    flood_col = s1.filterDate(start_ee, end_ee)

    ref_count = ref_col.size().getInfo()
    flood_count = flood_col.size().getInfo()
    if ref_count == 0:
        raise ValueError(
            f"No Sentinel-1 imagery found in the 30 days before {start_date} "
            f"to use as a pre-flood reference. Try a later start date."
        )
    if flood_count == 0:
        raise ValueError(
            f"No Sentinel-1 imagery found between {start_date} and {end_date}. "
            f"Sentinel-1's revisit time is roughly 6-12 days depending on region -- "
            f"try widening the date range."
        )

    # Speckle filter: focal median smooths pixel-to-pixel noise inherent to
    # SAR before we threshold on it. 50m kernel ~= 5 GRD pixels (10m/px).
    def _speckle_filter(img):
        return img.focalMedian(50, "circle", "meters")

    before = _speckle_filter(ref_col.mean())
    after = _speckle_filter(flood_col.mean())

    # Flood candidate = significant backscatter drop (smooth surface water
    # reflects radar away from the sensor instead of scattering it back).
    diff = before.subtract(after)
    raw_flood_mask = diff.gt(3)  # >3 dB drop

    # Exclude permanent water (JRC occurrence >50% treated as "usually
    # water") -- a lake/river isn't a new flood just because it's water in
    # both composites.
    permanent_water = (
        ee.Image("JRC/GSW1_4/GlobalSurfaceWater")
        .select("occurrence")
        .gt(50)
        .unmask(0)
    )

    # Exclude steep terrain -- radar shadow/layover on slopes mimics a
    # backscatter drop without any actual flooding.
    slope = ee.Terrain.slope(ee.Image("USGS/SRTMGL1_003"))
    steep_terrain = slope.gt(5)

    flood_mask = raw_flood_mask.And(permanent_water.Not()).And(steep_terrain.Not())

    flood_km2 = _calc_area_km2(flood_mask, region, scale=10)
    raw_km2 = _calc_area_km2(raw_flood_mask, region, scale=10)

    return {
        "metrics": {
            "flood_area_km2": round(flood_km2, 4),
            # Transparency: how much of the raw backscatter-drop signal got
            # filtered out as permanent water / steep terrain, so this
            # isn't an opaque number -- see the trust/verification
            # discussion this was built to address.
            "raw_backscatter_drop_km2": round(raw_km2, 4),
            "reference_scenes_used": ref_count,
            "flood_period_scenes_used": flood_count,
        },
        "ee_image": flood_mask,
        "ee_geometry": region,
    }


def compute_fire_detection(aoi: Dict, start_date: str, end_date: str) -> Dict:
    """Burn scar detection using MODIS burned area."""
    logger.info(f"GEE: fire_detection {start_date} → {end_date}")
    _require_start_after(start_date, "2000-11-01", "MODIS Burned Area")
    end_ee = _cap_end_date(end_date)
    region = _polygon_geometry(aoi)
    start_ee = ee.Date(start_date)

    modis_ba = (
        ee.ImageCollection("MODIS/061/MCD64A1")
        .filterBounds(region)
        .filterDate(start_ee, end_ee)
        .select("BurnDate")
    )

    burned_mask = modis_ba.max().gt(0)
    burned_km2 = _calc_area_km2(burned_mask, region, scale=500)

    # Count fire events using MODIS active fire
    active_fire = (
        ee.ImageCollection("MODIS/061/MOD14A1")
        .filterBounds(region)
        .filterDate(start_ee, end_ee)
        .select("FireMask")
    )
    active_count = active_fire.filter(ee.Filter.gt("system:asset_size", 0)).size().getInfo()

    return {
        "metrics": {
            "burned_area_km2": round(burned_km2, 4),
            "fire_event_count": float(active_count),
        },
        "ee_image": burned_mask,
        "ee_geometry": region,
    }


def compute_drought_index(aoi: Dict, start_date: str, end_date: str) -> Dict:
    """Drought severity using NDDI (Normalized Difference Drought Index)."""
    logger.info(f"GEE: drought_index {start_date} → {end_date}")
    _require_start_after(start_date, "2015-06-23", "Sentinel-2")
    end_ee = _cap_end_date(end_date)
    region = _polygon_geometry(aoi)
    start_ee = ee.Date(start_date)

    def get_nddi(start, end):
        col_raw = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(region)
            .filterDate(start, end)
            .map(_mask_s2_clouds)
        )
        if col_raw.size().getInfo() == 0:
            raise ValueError(
                f"No cloud-free Sentinel-2 imagery found for {start.format('YYYY-MM-dd').getInfo()} "
                f"– {end.format('YYYY-MM-dd').getInfo()}."
            )
        col = col_raw.median()
        ndvi = col.normalizedDifference(["B8", "B4"]).rename("NDVI")
        ndwi = col.normalizedDifference(["B3", "B8"]).rename("NDWI")
        # NDDI = (NDVI - NDWI) / (NDVI + NDWI)
        return ndvi.subtract(ndwi).divide(ndvi.add(ndwi)).rename("NDDI")

    start_nddi = get_nddi(start_ee, start_ee.advance(1, "year"))
    end_nddi = get_nddi(end_ee.advance(-1, "year"), end_ee)

    # High NDDI = drought stress; threshold > 0.5
    drought_mask = end_nddi.gt(0.5)
    severe_drought_mask = end_nddi.gt(0.7)

    drought_km2 = _calc_area_km2(drought_mask, region)
    severe_km2 = _calc_area_km2(severe_drought_mask, region)
    region_area_km2 = _region_area_km2(region)
    drought_affected_pct = round(min(max(drought_km2 / region_area_km2 * 100, 0), 100), 4) if region_area_km2 > 0 else None
    severe_drought_pct = round(min(max(severe_km2 / region_area_km2 * 100, 0), 100), 4) if region_area_km2 > 0 else None

    avg_nddi_start = start_nddi.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=region, scale=30, maxPixels=1e9,
        bestEffort=True, tileScale=4,
    ).get("NDDI").getInfo() or 0
    avg_nddi_end = end_nddi.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=region, scale=30, maxPixels=1e9,
        bestEffort=True, tileScale=4,
    ).get("NDDI").getInfo() or 0

    return {
        "metrics": {
            "drought_affected_km2": round(drought_km2, 4),
            "severe_drought_km2": round(severe_km2, 4),
            "region_area_km2": round(region_area_km2, 4),
            "drought_affected_pct": drought_affected_pct,
            "severe_drought_pct": severe_drought_pct,
            "avg_nddi_start": round(avg_nddi_start, 4),
            "avg_nddi_end": round(avg_nddi_end, 4),
            "nddi_change": round(avg_nddi_end - avg_nddi_start, 4),
        },
        "ee_image": drought_mask,
        "ee_geometry": region,
    }


def compute_land_surface_temperature(aoi: Dict, start_date: str, end_date: str) -> Dict:
    """LST analysis using Landsat 8/9."""
    logger.info(f"GEE: LST {start_date} → {end_date}")
    _require_start_after(start_date, "2013-03-18", "Landsat 8")
    end_ee = _cap_end_date(end_date)
    region = _polygon_geometry(aoi)
    start_ee = ee.Date(start_date)

    def get_lst(start, end):
        col = (
            ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
            .filterBounds(region)
            .filterDate(start, end)
            .filter(ee.Filter.lt("CLOUD_COVER", 20))
        )
        if col.size().getInfo() == 0:
            # Fallback to Landsat 9
            col = (
                ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
                .filterBounds(region)
                .filterDate(start, end)
                .filter(ee.Filter.lt("CLOUD_COVER", 20))
            )
        # ST_B10 is the surface temperature band (in Kelvin * 0.00341802 + 149.0)
        lst = col.map(
            lambda img: img.select("ST_B10")
            .multiply(0.00341802)
            .add(149.0)
            .subtract(273.15)  # to Celsius
            .rename("LST_C")
        ).mean()
        return lst

    start_lst = get_lst(start_ee, start_ee.advance(1, "year"))
    end_lst = get_lst(end_ee.advance(-1, "year"), end_ee)

    # Stats
    def get_stats(img):
        return img.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.min(), sharedInputs=True)
                                     .combine(ee.Reducer.max(), sharedInputs=True),
            geometry=region, scale=100, maxPixels=1e9, bestEffort=True, tileScale=4,
        ).getInfo()

    start_stats = get_stats(start_lst)
    end_stats = get_stats(end_lst)

    # UHI mask: pixels > mean + 2°C
    mean_temp = end_lst.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=region, scale=100, maxPixels=1e9,
        bestEffort=True, tileScale=4,
    ).get("LST_C")
    uhi_mask = end_lst.gt(ee.Image.constant(mean_temp).add(2))
    uhi_km2 = _calc_area_km2(uhi_mask, region, scale=100)

    return {
        "metrics": {
            "start_mean_lst_c": round(start_stats.get("LST_C_mean") or 0, 2),
            "end_mean_lst_c": round(end_stats.get("LST_C_mean") or 0, 2),
            "end_min_lst_c": round(end_stats.get("LST_C_min") or 0, 2),
            "end_max_lst_c": round(end_stats.get("LST_C_max") or 0, 2),
            "lst_change_c": round((end_stats.get("LST_C_mean") or 0) - (start_stats.get("LST_C_mean") or 0), 2),
            "uhi_area_km2": round(uhi_km2, 4),
        },
        "ee_image": uhi_mask,
        "ee_geometry": region,
    }


def compute_temperature_context(aoi: Dict, as_of: str = None, recent_days: int = 90) -> Dict:
    """Recent land-surface-temperature reading for an AOI (mean/min/max over
    a single rolling window), for use as regional context alongside a risk
    report — NOT the two-window before/after change framing
    compute_land_surface_temperature() uses for the standalone LST report.
    Uses the same Landsat 8/9 ST_B10 source and Celsius conversion."""
    logger.info(f"GEE: temperature_context as_of={as_of}")
    region = _polygon_geometry(aoi)
    end_dt = ee.Date(as_of) if as_of else ee.Date(datetime.datetime.utcnow().strftime("%Y-%m-%d"))
    start_dt = end_dt.advance(-recent_days, "day")

    def _collection(start, end):
        col = (
            ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
            .filterBounds(region)
            .filterDate(start, end)
            .filter(ee.Filter.lt("CLOUD_COVER", 20))
        )
        if col.size().getInfo() == 0:
            col = (
                ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
                .filterBounds(region)
                .filterDate(start, end)
                .filter(ee.Filter.lt("CLOUD_COVER", 20))
            )
        return col

    col = _collection(start_dt, end_dt)
    if col.size().getInfo() == 0:
        return {"status": "no_data", "note": "No cloud-free Landsat 8/9 thermal imagery found for this AOI/period."}

    lst = col.map(
        lambda img: img.select("ST_B10").multiply(0.00341802).add(149.0).subtract(273.15).rename("LST_C")
    ).mean()

    stats = lst.reduceRegion(
        reducer=ee.Reducer.mean().combine(ee.Reducer.min(), sharedInputs=True)
                                 .combine(ee.Reducer.max(), sharedInputs=True),
        geometry=region, scale=100, maxPixels=1e9, bestEffort=True, tileScale=4,
    ).getInfo()

    mean_c = stats.get("LST_C_mean")
    if mean_c is None:
        return {"status": "no_data", "note": "No usable Landsat thermal pixels for this AOI/period."}

    return {
        "status": "ok",
        "mean_lst_c": round(mean_c, 2),
        "min_lst_c": round(stats.get("LST_C_min") or 0, 2),
        "max_lst_c": round(stats.get("LST_C_max") or 0, 2),
        "recent_days": recent_days,
        "resolution_note": "Landsat 8/9 thermal band, ~100 m (resampled) resolution.",
        "source": "LANDSAT/LC08-LC09/C02/T1_L2 (ST_B10)",
    }


def compute_deforestation(aoi: Dict, start_date: str, end_date: str) -> Dict:
    """Forest loss using Hansen Global Forest Watch."""
    logger.info(f"GEE: deforestation {start_date} → {end_date}")
    region = _polygon_geometry(aoi)

    # Parse year range from dates
    start_year = int(start_date[:4])
    end_year = int(end_date[:4])
    if start_year < 2001:
        start_year = 2001
    if end_year > 2023:
        end_year = 2023

    hansen = ee.Image("UMD/hansen/global_forest_change_2024_v1_12")
    loss_year = hansen.select("lossyear")
    tree_cover = hansen.select("treecover2000")

    # Only count pixels with >30% canopy cover
    forest_mask = tree_cover.gte(30)
    year_range_mask = loss_year.gte(start_year - 2000).And(loss_year.lte(end_year - 2000))
    loss_mask = forest_mask.And(year_range_mask)

    loss_km2 = _calc_area_km2(loss_mask, region, scale=30)
    total_forest_km2 = _calc_area_km2(forest_mask, region, scale=30)
    loss_pct = (loss_km2 / total_forest_km2 * 100) if total_forest_km2 > 0 else 0

    return {
        "metrics": {
            "forest_loss_km2": round(loss_km2, 4),
            "total_forest_2000_km2": round(total_forest_km2, 4),
            "loss_pct": round(loss_pct, 4),
            "analysis_years": float(end_year - start_year),
            "annual_loss_rate_km2": round(loss_km2 / max(end_year - start_year, 1), 4),
        },
        "ee_image": loss_mask,
        "ee_geometry": region,
    }


def compute_soil_moisture(aoi: Dict, start_date: str, end_date: str) -> Dict:
    """Soil moisture using SMAP L4 (NASA/SMAP/SPL4SMGP/008).

    Was previously NASA_USDA/HSL/SMAP10KM_soil_moisture (band "ssm"), which
    GEE flags as deprecated in favor of this dataset. That old collection
    appears to have stopped receiving new imagery some time ago — every
    request for a recent window (e.g. the last 3 months) was returning zero
    images regardless of which AOI was queried, which is exactly what a
    frozen/discontinued collection looks like (a real per-region data gap
    would vary by region and season, not fail identically everywhere,
    every time). SPL4SMGP.008 is NASA's actively-maintained 3-hourly
    replacement (band "sm_surface")."""
    logger.info(f"GEE: soil_moisture {start_date} → {end_date}")
    _require_start_after(start_date, "2015-04-01", "SMAP")
    end_ee = _cap_end_date(end_date)
    region = _polygon_geometry(aoi)
    start_ee = ee.Date(start_date)

    smap = ee.ImageCollection("NASA/SMAP/SPL4SMGP/008").filterBounds(region)

    def get_sm_stats(start, end):
        col = smap.filterDate(start, end).select("sm_surface")
        if col.size().getInfo() == 0:
            return None
        img = col.mean()
        stats = img.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.min(), sharedInputs=True)
                                     .combine(ee.Reducer.max(), sharedInputs=True),
            geometry=region, scale=10000, maxPixels=1e9, bestEffort=True, tileScale=4,
        ).getInfo()
        # reduceRegion output keys are named after the (renamed) band, so
        # normalize back to the "ssm_*" shape the rest of this function
        # already expects rather than touching every call site below.
        if stats:
            stats = {k.replace("sm_surface", "ssm"): v for k, v in stats.items()}
        return stats

    start_stats = get_sm_stats(start_ee, start_ee.advance(3, "month"))
    end_stats = get_sm_stats(end_ee.advance(-3, "month"), end_ee)

    start_mean = (start_stats or {}).get("ssm_mean")
    end_mean = (end_stats or {}).get("ssm_mean")
    data_available = start_mean is not None and end_mean is not None

    # Dry stress mask: SM < 0.1 m³/m³
    end_window = smap.filterDate(end_ee.advance(-3, "month"), end_ee).select("sm_surface")
    if end_window.size().getInfo() == 0:
        # No SMAP coverage for this AOI/window — same condition get_sm_stats
        # already handles gracefully above; .mean() on an empty collection
        # returns a 0-band image, and comparing that against a constant
        # throws ("Image.lt: ... Got 0 and 1") rather than failing cleanly.
        # IMPORTANT: this is genuinely "we don't know", not "definitely zero
        # dry area" — reported as null, not 0.0, so it isn't mistaken for a
        # verified low-risk reading downstream.
        dry_km2 = None
        dry_pct = None
        dry_mask = ee.Image(0).clip(region)  # valid, empty mask — keeps ee_image usable downstream
    else:
        end_sm_img = end_window.mean()
        dry_mask = end_sm_img.lt(0.1)
        dry_km2 = round(_calc_area_km2(dry_mask, region, scale=10000), 4)
        region_area_km2 = _region_area_km2(region)
        dry_pct = round(min(max(dry_km2 / region_area_km2 * 100, 0), 100), 4) if region_area_km2 > 0 else None

    return {
        "metrics": {
            "start_avg_soil_moisture": round(start_mean, 4) if start_mean is not None else None,
            "end_avg_soil_moisture": round(end_mean, 4) if end_mean is not None else None,
            "moisture_change": round(end_mean - start_mean, 4) if data_available else None,
            "dry_stress_area_km2": dry_km2,
            "dry_stress_pct": dry_pct,
            "end_min_sm": round((end_stats or {}).get("ssm_min"), 4) if (end_stats or {}).get("ssm_min") is not None else None,
            "end_max_sm": round((end_stats or {}).get("ssm_max"), 4) if (end_stats or {}).get("ssm_max") is not None else None,
            "data_available": data_available,
        },
        "ee_image": dry_mask,
        "ee_geometry": region,
    }
