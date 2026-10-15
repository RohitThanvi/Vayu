"""india_risk.py — India food-and-fuel pressure indicators, side by side.

Deliberately NOT a composite index. Combining oil, rupee and food prices into one number would need weights that nobody
can defend, so each indicator is shown on its own with where today's value sits in its own last-12-months range
(percentile) and its recent change, plus the state of the chokepoints Indian trade depends on. The reader sees every
input; nothing is hidden inside a score.

Prices are global reference futures/FX (Yahoo, ~15-20 min delayed), not Indian domestic prices: wheat, sugar and cotton
at home are also shaped by procurement, export policy and local supply. Indian mandi prices and rainfall/drought live in
the Agri tab. The RBI repo rate is intentionally absent — RBI has no official free API (see macro.py).
"""
import asyncio
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from . import watchlist
from .data_quality import describe

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60 * 60

# (id, Yahoo symbol, label, unit, group, why it matters)
INDICATORS = [
    ("brent", "BZ=F", "Brent crude", "USD/barrel", "fuel", "India relies heavily on imported crude oil, so oil prices feed directly into fuel costs and the import bill."),
    ("usdinr", "INR=X", "USD/INR", "INR per USD", "currency", "A higher number means a weaker rupee, which raises the rupee cost of everything India imports, fuel and edible oils included."),
    ("wheat", "ZW=F", "Wheat (CBOT futures)", "USD/bushel", "food", "Global reference price. Indian domestic wheat prices are also shaped by procurement and export policy."),
    ("sugar", "SB=F", "Sugar (ICE futures)", "USD/lb", "food", "Global reference price. Indian sugar is also shaped by cane output and export policy."),
    ("cotton", "CT=F", "Cotton (ICE futures)", "USD/lb", "food", "Global reference price for a major Indian crop and textile input."),
]
CHOKEPOINTS_OF_INTEREST = ("strait_of_hormuz", "strait_of_malacca", "bab_el_mandeb")   # Gulf oil, Asia trade, Europe-bound trade

_cache: Dict[str, Any] = {"data": None, "at": 0.0, "error": None}
_lock = threading.Lock()


def percentile_rank(closes: List[float], latest: float) -> Optional[float]:
    """Share (0-100) of the closes that are <= latest: where today sits in its own range."""
    if not closes:
        return None
    return round(100.0 * sum(1 for c in closes if c <= latest) / len(closes), 1)


def pct_change(closes: List[float], k: int) -> Optional[float]:
    if len(closes) <= k or closes[-1 - k] == 0:
        return None
    return round((closes[-1] / closes[-1 - k] - 1) * 100, 2)


def position_label(pct: Optional[float]) -> Optional[str]:
    """Quintile wording: a plain description of the percentile, not a signal."""
    if pct is None:
        return None
    return "top fifth of its 12-month range" if pct >= 80 else "bottom fifth of its 12-month range" if pct <= 20 else "middle of its 12-month range"


def build_indicator(spec, points: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    ind_id, symbol, label, unit, group, why = spec
    closes = [p["value"] for p in points if p.get("value") is not None]
    if len(closes) < 30:
        return None
    window = closes[-252:]
    pct = percentile_rank(window, closes[-1])
    return {"id": ind_id, "symbol": symbol, "label": label, "unit": unit, "group": group, "why": why,
            "latest": closes[-1], "as_of": points[-1].get("date"),
            "change_1m_pct": pct_change(closes, 21), "change_3m_pct": pct_change(closes, 63), "change_1y_pct": pct_change(closes, 252),
            "percentile_12m": pct, "position": position_label(pct), "low_12m": min(window), "high_12m": max(window),
            "sparkline": [round(c, 4) for c in window[::max(1, len(window) // 60)]]}


async def get_india_risk() -> Dict[str, Any]:
    with _lock:
        if _cache["data"] and (time.time() - _cache["at"]) < CACHE_TTL_SECONDS:
            return _cache["data"]
    histories = await asyncio.gather(*(watchlist.get_history(s[1], "1y") for s in INDICATORS), return_exceptions=True)
    indicators = []
    for spec, h in zip(INDICATORS, histories):
        if isinstance(h, Exception) or not h.get("points"):
            continue
        ind = build_indicator(spec, h["points"])
        if ind:
            indicators.append(ind)
    with _lock:
        if indicators:
            _cache["data"] = {"indicators": indicators, "expected": len(INDICATORS)}
            _cache["at"], _cache["error"] = time.time(), (None if len(indicators) == len(INDICATORS) else f"only {len(indicators)}/{len(INDICATORS)} indicators available this cycle")
        else:
            _cache["error"] = "no indicator data available this cycle"
        data, at, err = _cache["data"], _cache["at"], _cache["error"]
    if not data:
        return {"indicators": [], "expected": len(INDICATORS), "data_quality": describe(0.0, CACHE_TTL_SECONDS, err)}
    return {**data, "data_quality": describe(at, CACHE_TTL_SECONDS, err)}


def attach_chokepoints(result: Dict[str, Any], risk_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_id = {r["chokepoint"]: r for r in risk_list}
    return {**result, "chokepoints": [
        {"chokepoint": cp, "display": by_id[cp]["chokepoint_display"], "band": by_id[cp]["band"], "score": by_id[cp]["score"]}
        for cp in CHOKEPOINTS_OF_INTEREST if cp in by_id],
        "method": ("Each indicator shows where today's value sits within its own last 12 months (percentile of daily closes) and its recent "
                   "change. There is deliberately no combined index: weights would be invented. Prices are global reference prices, not Indian "
                   "domestic prices. Chokepoint bands are Vayu's business-risk bands (traffic, news tone, seismic, sanctions).")}
