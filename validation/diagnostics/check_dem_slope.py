#!/usr/bin/env python3
"""
check_dem_slope.py - LIVE check (needs your local Earth Engine credentials) of the slope the Crops tab samples.

Why: in the validation profiles NO district has a mean slope above 2.3%, including Himalayan ones (Uttarkashi 1.9%, Sikkim 2.2%). Real values are
tens of percent, so the slope the engine uses is almost certainly wrong. Both the Crops sampler and the Spectra terrain tool build it as
    ee.Terrain.slope( ImageCollection('COPERNICUS/DEM/GLO30_2024_1').select('DEM').mosaic() )
which is a mosaic with no fixed projection. This script computes mean slope for the same AOIs several ways so the correct one can be chosen.

  cd backend
  python ../validation/diagnostics/check_dem_slope.py ../validation/aoi/uttarakhand__uttarkashi.geojson ../validation/aoi/sikkim__west.geojson ../validation/aoi/punjab__sangrur.geojson

Expected: Uttarkashi / Sikkim mean slope of roughly 20-40 degrees, Sangrur well under 1 degree. The method whose numbers look like that (and agree with the
independent SRTM column) is the one to use.
"""
import json, math, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
import ee                                                            # noqa: E402
from app.services import gee_client                                  # noqa: E402

gee_client._initialize_gee()
if not gee_client.GEE_READY:
    sys.exit(f"Earth Engine not ready: {gee_client.GEE_INIT_ERROR}")


def mean_slope(img, region, scale=30):
    v = img.reduceRegion(reducer=ee.Reducer.mean(), geometry=region, scale=scale, maxPixels=1e9, bestEffort=True, tileScale=4).getInfo()
    return next(iter(v.values()))


coll = ee.ImageCollection("COPERNICUS/DEM/GLO30_2024_1").select("DEM")
first_proj = coll.first().projection()
print(f"{'AOI':34s} {'A current (mosaic)':>20s} {'B setDefaultProjection':>24s} {'C reproject 30m':>16s} {'D SRTM (independent)':>22s}")
for path in sys.argv[1:]:
    aoi = json.loads(Path(path).read_text())
    region = gee_client._polygon_geometry(aoi)
    mosaic = coll.mosaic()
    a = mean_slope(ee.Terrain.slope(mosaic), region)                                                  # what the app does today
    b = mean_slope(ee.Terrain.slope(mosaic.setDefaultProjection(first_proj)), region)                 # keep the tiles' native projection
    c = mean_slope(ee.Terrain.slope(mosaic.reproject(first_proj.atScale(30))), region)                # force a 30 m grid
    d = mean_slope(ee.Terrain.slope(ee.Image("USGS/SRTMGL1_003")), region)                            # separate DEM, native projection
    f = lambda v: "n/a" if v is None else f"{v:6.2f} deg ({math.tan(math.radians(v)) * 100:5.1f}%)"
    print(f"{Path(path).stem:34s} {f(a):>20s} {f(b):>24s} {f(c):>16s} {f(d):>22s}")
