"""
Wind vector field — builds the data leaflet-velocity needs to render an
*animated* wind layer (moving particles along real flow direction), as
opposed to OpenWeatherMap's wind_new tile, which is a static image of wind
*speed* only (no direction, no motion).

leaflet-velocity expects two records (U-component and V-component of wind),
each a GRIB-like {header, data} object — see
https://github.com/onaci/leaflet-velocity and
https://wlog.viltstigen.se/articles/2021/11/08/visualizing-wind-using-leaflet/
for the exact shape. `data` is a flat list of nx*ny floats, scanning
west->east across each row starting at the northernmost row (la1, lo1).

Source: Open-Meteo's free forecast API (open-meteo.com) — no API key
required for non-commercial use, and it supports up to 1000 coordinate
pairs per request, which is what makes fetching a whole-globe grid in a
handful of calls practical instead of needing our own weather model.
"""

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
MAX_LOCATIONS_PER_CALL = 300  # kept well under Open-Meteo's documented 1000-per-request
                               # limit -- a 1000-coordinate GET produces a ~14k-char URL,
                               # which risks silent rejection by an intermediate proxy even
                               # though Open-Meteo itself documents supporting that count

# Grid resolution. 5 degrees keeps the whole-globe grid at a handful of
# Open-Meteo calls (2520 points / 1000 per call = 3 calls) and refreshes
# fast; leaflet-velocity interpolates + animates particles across it client
# side, so it still reads as continuous flow rather than a blocky grid.
LAT_STEP = 5.0
LON_STEP = 5.0
LA1, LA2 = 85.0, -85.0    # northernmost / southernmost row
# leaflet-velocity (and the GRIB convention it's built on) expects longitude
# in 0-360 form, NOT signed -180..180 -- its wrap/interpolation math assumes
# the grid starts at 0. Using -180 here was the bug: the fetch succeeds and
# the layer gets added, but the coordinate math silently breaks so nothing
# visibly renders. Open-Meteo itself wants signed -180..180, so that
# conversion happens only at the point of querying (see refresh()), not here.
LO1, LO2 = 0.0, 355.0     # 0..355 in 5deg steps = full 360 span, no seam dupe


class WindFieldStore:
    def __init__(self):
        self._data: Optional[list[dict]] = None
        self._lock = asyncio.Lock()
        self._last_refresh: Optional[datetime] = None

    def get(self) -> Optional[list[dict]]:
        return self._data

    def get_stats(self) -> dict:
        return {
            "available": self._data is not None,
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
        }

    async def refresh(self):
        lats, lons_360, lons_signed = [], [], []
        lat = LA1
        while lat >= LA2 - 1e-6:
            lo = LO1
            while lo <= LO2 + 1e-6:
                lats.append(round(lat, 2))
                lons_360.append(round(lo, 2))
                # Open-Meteo wants signed longitude -- 0..180 stays as-is,
                # 180..360 maps to -180..0.
                lons_signed.append(round(lo if lo <= 180 else lo - 360, 2))
                lo += LON_STEP
            lat -= LAT_STEP
        nx = int(round((LO2 - LO1) / LON_STEP)) + 1
        ny = int(round((LA1 - LA2) / LAT_STEP)) + 1
        if nx * ny != len(lats):
            logger.error(f"wind field grid size mismatch: {nx}x{ny} != {len(lats)} points, aborting refresh")
            return

        speeds: list[Optional[float]] = [None] * len(lats)
        dirs: list[Optional[float]] = [None] * len(lats)

        async with httpx.AsyncClient(timeout=30) as client:
            chunks_ok = 0
            for start in range(0, len(lats), MAX_LOCATIONS_PER_CALL):
                chunk_lats = lats[start:start + MAX_LOCATIONS_PER_CALL]
                chunk_lons = lons_signed[start:start + MAX_LOCATIONS_PER_CALL]
                params = {
                    "latitude": ",".join(str(v) for v in chunk_lats),
                    "longitude": ",".join(str(v) for v in chunk_lons),
                    "current": "wind_speed_10m,wind_direction_10m",
                    "wind_speed_unit": "ms",
                }
                try:
                    resp = await client.get(OPEN_METEO_URL, params=params)
                    resp.raise_for_status()
                    results = resp.json()
                    # Open-Meteo returns a bare object (not a list) for a
                    # single-location request; always a list for >1 locations.
                    if isinstance(results, dict):
                        results = [results]
                    for i, r in enumerate(results):
                        cur = r.get("current", {})
                        idx = start + i
                        speeds[idx] = cur.get("wind_speed_10m")
                        dirs[idx] = cur.get("wind_direction_10m")
                    chunks_ok += 1
                except Exception as e:
                    # One bad chunk (timeout, transient 5xx) shouldn't kill
                    # the whole refresh -- log and move on, leaving that
                    # chunk's points at 0,0 wind rather than aborting.
                    logger.warning(f"wind field chunk {start}-{start+len(chunk_lats)} failed: {e}")

            if chunks_ok == 0:
                logger.error("wind field refresh: every chunk failed, keeping previous data (if any)")
                return

        u_vals, v_vals = [], []
        for s, d in zip(speeds, dirs):
            if s is None or d is None:
                u_vals.append(0.0)
                v_vals.append(0.0)
                continue
            rad = math.radians(d)
            # Open-Meteo gives meteorological direction (the direction the
            # wind is blowing FROM), which is the standard convention this
            # -sin/-cos conversion to U/V (eastward/northward) expects.
            u_vals.append(round(-s * math.sin(rad), 2))
            v_vals.append(round(-s * math.cos(rad), 2))

        ref_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        header_common = {
            "lo1": LO1, "la1": LA1, "lo2": LO2, "la2": LA2,
            "dx": LON_STEP, "dy": LAT_STEP, "nx": nx, "ny": ny,
            "refTime": ref_time,
        }
        data = [
            {"header": {**header_common, "parameterCategory": 2, "parameterNumber": 2}, "data": u_vals},
            {"header": {**header_common, "parameterCategory": 2, "parameterNumber": 3}, "data": v_vals},
        ]

        async with self._lock:
            self._data = data
            self._last_refresh = datetime.now(timezone.utc)
        logger.info(f"wind field refreshed: {nx}x{ny} grid ({len(u_vals)} points)")


wind_field_store = WindFieldStore()
