"""
mandi.py — market (mandi) price overlay.

Ties a risk/condition read to "what's this crop worth right now" — the
harvest-timing decision no pure remote-sensing competitor surfaces well.

Uses data.gov.in's public Agmarknet daily mandi price API (India). Generalized
by commodity + state/district params, not hardcoded to one crop or region.
Needs a free API key from data.gov.in (DATA_GOV_IN_API_KEY env var) — the
resource works without a key too using the shared demo key, at low rate
limits, so this degrades gracefully rather than hard-failing if unset.
"""

import logging
import os
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"  # Agmarknet current daily prices
BASE_URL = f"https://api.data.gov.in/resource/{RESOURCE_ID}"
DEMO_KEY = "579b464db66ec23bdd000001cdd3946e44ce4aad7209ff7b23ac571"  # data.gov.in public demo key, low rate limit


async def get_mandi_prices(commodity: Optional[str] = None, state: Optional[str] = None,
                            district: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
    api_key = os.environ.get("DATA_GOV_IN_API_KEY", DEMO_KEY)
    params = {
        "api-key": api_key,
        "format": "json",
        "limit": str(limit),
    }
    if commodity:
        params["filters[commodity]"] = commodity
    if state:
        params["filters[state]"] = state
    if district:
        params["filters[district]"] = district

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(BASE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"mandi price fetch failed: {e}")
        return {"records": [], "error": str(e), "source": "data.gov.in Agmarknet"}

    records = data.get("records", [])
    parsed = [
        {
            "commodity": r.get("commodity"),
            "variety": r.get("variety"),
            "market": r.get("market"),
            "district": r.get("district"),
            "state": r.get("state"),
            "min_price": r.get("min_price"),
            "max_price": r.get("max_price"),
            "modal_price": r.get("modal_price"),
            "arrival_date": r.get("arrival_date"),
        }
        for r in records
    ]
    return {"records": parsed, "count": len(parsed), "source": "data.gov.in Agmarknet"}
