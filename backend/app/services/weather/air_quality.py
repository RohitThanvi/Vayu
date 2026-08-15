"""
air_quality.py — point air-quality lookup via Open-Meteo's free Air Quality
API (air-quality-api.open-meteo.com) — same no-key provider already used
for the wind layer, so no new API key/account is needed.
"""

import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

# US AQI breakpoints -> (label, color) for a quick visual read, matching the
# standard EPA categories most people already recognize.
_AQI_BANDS = [
    (50, "Good", "#4a7c59"),
    (100, "Moderate", "#c9933a"),
    (150, "Unhealthy for Sensitive Groups", "#d97a41"),
    (200, "Unhealthy", "#c96a3a"),
    (300, "Very Unhealthy", "#8b2020"),
    (float("inf"), "Hazardous", "#5c1a1a"),
]


def _band_for_aqi(aqi: Optional[float]) -> Dict[str, str]:
    if aqi is None:
        return {"label": "Unknown", "color": "#5c6673"}
    for threshold, label, color in _AQI_BANDS:
        if aqi <= threshold:
            return {"label": label, "color": color}
    return {"label": "Hazardous", "color": "#5c1a1a"}


async def get_air_quality(lat: float, lon: float) -> Dict[str, Any]:
    params = {
        "latitude": lat, "longitude": lon,
        "current": "pm2_5,pm10,us_aqi,carbon_monoxide,nitrogen_dioxide,ozone,sulphur_dioxide",
        "timezone": "auto",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(AIR_QUALITY_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"air quality fetch failed for ({lat},{lon}): {e}")
        return {"error": str(e)}

    current = data.get("current", {})
    us_aqi = current.get("us_aqi")
    band = _band_for_aqi(us_aqi)

    return {
        "lat": lat, "lon": lon,
        "us_aqi": us_aqi,
        "category": band["label"],
        "color": band["color"],
        "pm2_5": current.get("pm2_5"),
        "pm10": current.get("pm10"),
        "carbon_monoxide": current.get("carbon_monoxide"),
        "nitrogen_dioxide": current.get("nitrogen_dioxide"),
        "ozone": current.get("ozone"),
        "sulphur_dioxide": current.get("sulphur_dioxide"),
        "observed_at": current.get("time"),
        "source": "Open-Meteo Air Quality API",
    }
