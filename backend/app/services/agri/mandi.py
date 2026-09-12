"""
mandi.py — market (mandi) price overlay.

Ties a risk/condition read to "what's this crop worth right now" — the
harvest-timing decision no pure remote-sensing competitor surfaces well.

Uses data.gov.in's public Agmarknet daily mandi price API (India), the
same data.gov.in account/key as the CPCB air-quality feed (see
services/intel/air_quality.py) — one key covers both.
"""

import logging
from typing import Any, Dict, Optional

import httpx

from ...core.config import settings

logger = logging.getLogger(__name__)

RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"  # "Current Daily Price of Various Commodities from Various Markets (Mandi)"
BASE_URL = f"https://api.data.gov.in/resource/{RESOURCE_ID}"


async def get_mandi_prices(commodity: Optional[str] = None, state: Optional[str] = None,
                            district: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
    api_key = settings.AQI_API_KEY
    if not api_key:
        logger.warning("mandi price fetch skipped — AQI_API_KEY not configured")
        return {"records": [], "error": "AQI_API_KEY not configured", "source": "data.gov.in Agmarknet"}

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

    # Same fix as air_quality.py's CPCB fetch, which hit the identical
    # failure class in production (ReadTimeout — the connection succeeds,
    # data.gov.in just doesn't finish responding within 15s). This mandi
    # request is normally much smaller (default limit=20, a specific
    # commodity/state filter) than the 800+-station AQI pull, so it
    # doesn't need the same two-tier fallback — just a more realistic
    # single timeout. The `{e}` alone in the old log line could render
    # as a blank message for exceptions with no string body (exactly
    # what happened here) — now logs the exception TYPE too, so a
    # timeout is distinguishable from an auth error or anything else at
    # a glance instead of an empty-looking log line.
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.get(BASE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            if "records" not in data:
                # Same data.gov.in quirk as air_quality.py: an invalid/
                # not-yet-activated key or wrong resource id often comes
                # back as HTTP 200 with a {"message": "..."} body and no
                # "records" key, which raise_for_status() can't catch since
                # it only looks at the status code.
                detail = data.get("message") or data.get("error") or str(data)[:300]
                raise ValueError(f"data.gov.in returned no 'records' key — {detail}")
    except Exception as e:
        body_snippet = ""
        resp_obj = getattr(e, "response", None)
        if resp_obj is not None:
            try:
                body_snippet = f" — body: {resp_obj.text[:300]}"
            except Exception:
                pass
        logger.warning(f"mandi price fetch failed: {type(e).__name__}: {e}{body_snippet}")
        detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        return {"records": [], "error": detail + body_snippet, "source": "data.gov.in Agmarknet"}

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

