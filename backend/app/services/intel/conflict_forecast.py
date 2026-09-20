"""
conflict_forecast.py — ACLED's Conflict Alert System (CAST), a monthly
political-violence event-count FORECAST per country (and admin1), up to 6
months ahead (ACLED, released March 2023). Distinct from fetchers.py's
fetch_acled, which pulls individual historical/current CONFLICT EVENTS as
map markers — CAST returns country/region-level aggregate forecasts, not
point events, so it doesn't fit the live point-marker feed and lives here
as its own focused lookup instead (same shape as agri/groundwater.py or
intel/chokepoint_market_correlation.py: one fetch, one interpretation, not
another marker source).

CAST endpoint: https://acleddata.com/api/cast/read (confirmed against
ACLED's own docs, acleddata.com/api-documentation/cast-endpoint — not
guessed). Reuses fetchers.py's OAuth token function and its module-level
token cache: same myACLED account, same bearer token, a different read
endpoint — no reason to authenticate twice. ACLED describes CAST as
something you "gain access to" as a step separate from base event-feed
access, so a working fetch_acled account can still be denied CAST
specifically; that's handled below as its own clearly-labeled status
rather than folded into a generic error.

CAST is methodologically a forecast, not a measurement — ACLED's own CAST
methodology guidance is explicit that these are indicative, not
certainties. That caveat travels with every "ok" response this module
returns, not just a docstring mention.
"""

import logging
from typing import Any, Dict, Optional

import httpx

from .fetchers import _get_acled_token

logger = logging.getLogger(__name__)

ACLED_CAST_URL = "https://acleddata.com/api/cast/read"

# Confirmed directly against ACLED's CAST endpoint docs and the ACLED CAST
# API User Guide (both cited above): country, admin1, month, year,
# battles_forecast, vac_forecast, total_forecast, total_observed,
# battles_observed, vac_observed, timestamp. erv_forecast is inferred by
# the same battles/vac/erv naming pattern ACLED uses for its three core
# event-type buckets (Battles / Explosions-Remote-violence / Violence
# Against Civilians) rather than independently confirmed in a quoted field
# table — requested defensively (.get() with a None default below), so if
# this one name is wrong the rest of the response is unaffected.
CAST_FIELDS = ("country|admin1|month|year|battles_forecast|erv_forecast|vac_forecast|total_forecast|"
               "battles_observed|erv_observed|vac_observed|total_observed|timestamp")

# CAST forecasts up to this many months ahead — ACLED's own stated horizon
# for the product, not an arbitrary choice on Vayu's side.
FORECAST_HORIZON_MONTHS = 6


async def fetch_conflict_forecast(client: httpx.AsyncClient, email: str, password: str, country: str) -> Dict[str, Any]:
    if not email or not password:
        return {"status": "no_credentials", "note": "ACLED credentials not configured."}
    if not country:
        return {"status": "error", "note": "country is required."}

    token = await _get_acled_token(client, email, password)
    if not token:
        return {"status": "auth_failed", "note": "ACLED OAuth token request failed — see server logs."}

    headers = {"Authorization": f"Bearer {token}"}
    params = {"country": country, "fields": CAST_FIELDS, "limit": 100}

    try:
        resp = await client.get(ACLED_CAST_URL, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        rows = resp.json().get("data", [])
    except httpx.HTTPStatusError as e:
        # Log the response BODY, not just the bare exception — ACLED's
        # error responses are usually JSON with a real explanation, and
        # the previous version was only logging str(e) (just the status
        # line), which is why a 401 here showed no more detail than "401
        # Unauthorized" with nothing to actually debug from.
        body_snippet = e.response.text[:300] if e.response is not None else ""
        logger.warning(f"ACLED CAST: {e.response.status_code} for country={country!r} — body: {body_snippet}")
        if e.response.status_code in (401, 403):
            # Per ACLED's own error-code table (acleddata.com/api-documentation/
            # elements-acleds-api), 401 = "incorrect auth token" and 403 =
            # "Access denied" (account isn't in the API access group, or
            # similar). Both observed here with a token that DOES work for
            # the main event feed (fetch_acled) — the most likely read is
            # that CAST specifically isn't enabled for this myACLED
            # account (ACLED describes CAST as a separately-granted
            # access), but a bare 401/403 alone can't fully distinguish
            # that from a token/scope mismatch specific to this endpoint.
            # Stated as the likely explanation, not a certainty.
            return {"status": "no_cast_access",
                    "note": f"ACLED rejected this request to the CAST endpoint ({e.response.status_code}), "
                            f"even though the same account's credentials work for the main event feed. Most "
                            f"likely explanation: CAST access isn't enabled for this myACLED account — ACLED "
                            f"grants it separately from base API access, via their Access Team. Worth checking "
                            f"your myACLED dashboard, or asking ACLED support to confirm CAST is enabled."}
        logger.error(f"ACLED CAST fetch error: {e}")
        return {"status": "error", "note": str(e)}
    except Exception as e:
        logger.error(f"ACLED CAST fetch error: {e}")
        return {"status": "error", "note": str(e)}

    if not rows:
        return {"status": "no_data",
                "note": f"No CAST forecast rows for '{country}' — check the name matches how ACLED names it "
                        f"(e.g. as it appears in the event feed)."}

    months = []
    for r in rows:
        try:
            months.append({
                "year": int(r["year"]), "month": int(r["month"]),
                "admin1": (r.get("admin1") or None),
                "total_forecast": r.get("total_forecast"),
                "battles_forecast": r.get("battles_forecast"),
                "erv_forecast": r.get("erv_forecast"),
                "vac_forecast": r.get("vac_forecast"),
                "total_observed": r.get("total_observed"),
                "battles_observed": r.get("battles_observed"),
                "erv_observed": r.get("erv_observed"),
                "vac_observed": r.get("vac_observed"),
            })
        except (KeyError, ValueError, TypeError):
            continue
    months.sort(key=lambda m: (m["year"], m["month"]))

    # Country-level rows only (admin1 null/blank) for the headline trend —
    # CAST also returns per-admin1 rows when available, which would
    # otherwise get double-counted against the country total if summed in.
    country_months = [m for m in months if not m["admin1"]]
    forecast_ahead = [m for m in country_months if m["total_observed"] is None]
    recent_observed = [m for m in country_months if m["total_observed"] is not None]

    trend = None
    vals = [m["total_forecast"] for m in forecast_ahead if m["total_forecast"] is not None]
    if len(vals) >= 2:
        trend = "rising" if vals[-1] > vals[0] * 1.15 else "declining" if vals[-1] < vals[0] * 0.85 else "stable"

    return {
        "status": "ok",
        "country": country,
        "forecast_months": forecast_ahead[-FORECAST_HORIZON_MONTHS:],
        "recent_observed_months": recent_observed[-FORECAST_HORIZON_MONTHS:],
        "forecast_trend": trend,
        "method": (
            "ACLED Conflict Alert System (CAST), released March 2023 — a monthly forecast of political "
            "violence event counts per country, up to 6 months ahead, split into battles / explosions-remote-"
            "violence / violence-against-civilians. CAST forecasts are explicitly indicative per ACLED's own "
            "methodology guidance — not certainties, and not a substitute for the actual event feed."
        ),
    }
