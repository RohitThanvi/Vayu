"""
business_risk.py — the single synthesized number this whole feature
set is building toward: a 0-100 risk score per monitored chokepoint,
blending every signal this project tracks, rather than leaving them as
separate panels the reader has to mentally combine themselves.

Deliberately simple, deterministic, documented weights — no ML model,
no opaque scoring. An analyst using this needs to see exactly why the
number is what it is; a black-box score is worse than no score if you
can't audit it. Mirrors the same philosophy as the Agri side's drought
composite score (risk_scoring.py) — same idea, business-side inputs.

Inputs (each already built elsewhere in this project) and their
weight in the final 0-100 score:
  40%  Chokepoint vessel-traffic anomaly   (timeseries_store baseline z-score)
  20%  Nearby GDELT regional tone          (geo_tone.py)
  20%  Nearby seismic disruption           (seismic_disruption.py)
  20%  OFAC sanctions hits in the area     (sanctions.py)

Commodity volatility deliberately isn't a fifth weighted input here:
it's downstream of the other four (a real chokepoint disruption is
usually WHY a commodity moves), so including it as an independent
input would partly double-count the same underlying event. It's
surfaced alongside the score instead, as context, not folded into it.
"""

import math
from typing import Any, Optional

from . import timeseries_store
from . import geo_tone as geo_tone_mod
from . import seismic_disruption
from . import sanctions as sanctions_mod
from . import commodity_prices
from .supply_chain import CHOKEPOINT_COMMODITIES, CHOKEPOINT_DISPLAY
from .vessel_store import CHOKEPOINTS


def _chokepoint_center(cp_id: str) -> tuple[float, float]:
    (lat1, lon1), (lat2, lon2) = CHOKEPOINTS[cp_id]
    return (lat1 + lat2) / 2, (lon1 + lon2) / 2


def _traffic_component(cp_id: str, current_count: int) -> tuple[float, dict]:
    """0-100, driven by |z_score| — a z-score of 2+ (the same threshold
    timeseries_store already calls 'disrupted') maps to 100."""
    baseline = timeseries_store.get_chokepoint_baseline(cp_id, current_count)
    z = baseline.get("z_score")
    if z is None:
        return 0.0, baseline  # insufficient history — don't let an unknown pull the score down OR up
    score = min(100.0, abs(z) / 2.0 * 100.0)
    return score, baseline


def _tone_component(cp_id: str, gdelt_events: list[dict]) -> tuple[float, Optional[dict]]:
    """0-100, from the tone of the nearest regional cell to this
    chokepoint (see geo_tone.py) — tone <= -5 (geo_tone's own
    'deteriorating' threshold) maps to 100."""
    regions = geo_tone_mod.compute_tone_regions(gdelt_events, min_events=1)
    if not regions:
        return 0.0, None
    c_lat, c_lon = _chokepoint_center(cp_id)
    nearest = min(regions, key=lambda r: (r["lat"] - c_lat) ** 2 + (r["lon"] - c_lon) ** 2)
    dist_deg = math.hypot(nearest["lat"] - c_lat, nearest["lon"] - c_lon)
    if dist_deg > 15:  # too far to be "near" this chokepoint at all
        return 0.0, None
    tone = nearest["avg_tone"]
    score = min(100.0, max(0.0, -tone / 5.0 * 100.0))
    return score, nearest


def _seismic_component(cp_id: str, usgs_events: list[dict]) -> tuple[float, list[dict]]:
    """0-100, from the strongest nearby quake — magnitude 7+ maps to 100."""
    disruptions = [d for d in seismic_disruption.find_disruptions(usgs_events, min_magnitude=4.5) if d["chokepoint"] == cp_id]
    if not disruptions:
        return 0.0, []
    strongest = max(d["magnitude"] for d in disruptions)
    score = min(100.0, max(0.0, (strongest - 4.5) / (7.0 - 4.5) * 100.0))
    return score, disruptions


def _sanctions_component(cp_id: str, sanctioned_hits: list[dict]) -> tuple[float, list[dict]]:
    """0-100 — binary-ish: any sanctioned vessel actively in a chokepoint
    is a serious enough signal that even one hit scores high (70), scaling
    up toward 100 as more are found."""
    if not sanctioned_hits:
        return 0.0, []
    score = min(100.0, 70.0 + (len(sanctioned_hits) - 1) * 10.0)
    return score, sanctioned_hits


WEIGHTS = {"traffic": 0.40, "tone": 0.20, "seismic": 0.20, "sanctions": 0.20}


async def score_chokepoint(cp_id: str, current_vessel_count: int, gdelt_events: list[dict],
                            usgs_events: list[dict], vessels_in_area: list[dict]) -> dict[str, Any]:
    traffic_score, traffic_detail = _traffic_component(cp_id, current_vessel_count)
    tone_score, tone_detail = _tone_component(cp_id, gdelt_events)
    seismic_score, seismic_detail = _seismic_component(cp_id, usgs_events)
    sanctioned = await sanctions_mod.screen_vessels(vessels_in_area)
    sanctions_score, sanctions_detail = _sanctions_component(cp_id, sanctioned)

    total = (
        traffic_score * WEIGHTS["traffic"] + tone_score * WEIGHTS["tone"] +
        seismic_score * WEIGHTS["seismic"] + sanctions_score * WEIGHTS["sanctions"]
    )

    watch_commodities = CHOKEPOINT_COMMODITIES.get(cp_id, [])
    all_commodities = {c["symbol"]: c for c in commodity_prices.get_commodities().get("commodities", [])}
    commodity_moves = [all_commodities[sym] for sym in watch_commodities if sym in all_commodities]

    band = "critical" if total >= 60 else "elevated" if total >= 30 else "normal"

    return {
        "chokepoint": cp_id,
        "chokepoint_display": CHOKEPOINT_DISPLAY.get(cp_id, cp_id),
        "score": round(total, 1),
        "band": band,
        "components": {
            "traffic": {"score": round(traffic_score, 1), "weight": WEIGHTS["traffic"], "detail": traffic_detail},
            "tone": {"score": round(tone_score, 1), "weight": WEIGHTS["tone"], "detail": tone_detail},
            "seismic": {"score": round(seismic_score, 1), "weight": WEIGHTS["seismic"], "detail": seismic_detail},
            "sanctions": {"score": round(sanctions_score, 1), "weight": WEIGHTS["sanctions"], "detail": sanctions_detail},
        },
        "watch_commodities": commodity_moves,
    }
