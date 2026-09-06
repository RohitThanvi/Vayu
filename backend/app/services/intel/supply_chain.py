"""
supply_chain.py — correlates two feeds this project already tracks
separately (maritime chokepoint vessel traffic, global commodity
prices) into one view: for each monitored chokepoint, its current
traffic status (see timeseries_store.get_chokepoint_baseline) alongside
the day-over-day price move of commodities that route heavily through
it.

The chokepoint -> commodity mapping below is real-world trade-route
knowledge (which goods physically transit which strait/canal in
meaningful volume), not derived from any live trade-flow dataset — it's
an approximation, and is documented as such rather than presented as
measured. A narrative sentence is generated from simple, deterministic
logic (no LLM call) — this only needs to state two already-known facts
in one sentence, so an LLM round-trip would add latency and cost for
no real benefit over a same string template.
"""

from typing import Any, Dict, List

from .vessel_store import vessel_store, CHOKEPOINTS
from . import timeseries_store
from . import commodity_prices

CHOKEPOINT_DISPLAY = {
    "strait_of_hormuz":    "Strait of Hormuz",
    "strait_of_malacca":   "Strait of Malacca",
    "bab_el_mandeb":       "Bab-el-Mandeb Strait",
    "suez_canal":          "Suez Canal",
    "strait_of_gibraltar": "Strait of Gibraltar",
    "panama_canal":        "Panama Canal",
    "english_channel":     "English Channel",
}

# Approximate real-world trade relevance, not a measured trade-flow
# statistic — e.g. Hormuz carries roughly a fifth of global oil/LNG
# tanker traffic, so crude + natural gas are the obvious commodities to
# watch there; Panama carries a large share of US grain exports to Asia,
# so wheat/corn are the relevant ones there instead of energy.
CHOKEPOINT_COMMODITIES = {
    "strait_of_hormuz":    ["CL=F", "BZ=F", "NG=F"],
    "strait_of_malacca":   ["CL=F", "BZ=F", "HG=F"],
    "bab_el_mandeb":       ["CL=F", "BZ=F"],
    "suez_canal":          ["CL=F", "BZ=F", "NG=F"],
    "strait_of_gibraltar": ["CL=F", "BZ=F"],
    "panama_canal":        ["ZW=F", "ZC=F", "CL=F"],
    "english_channel":     ["BZ=F"],
}


def _narrative(display_name: str, status: str, deviation_pct, commodities: List[Dict[str, Any]]) -> str:
    if status == "insufficient_history":
        return f"{display_name} doesn't have enough traffic history yet to establish a baseline."

    moved = [c for c in commodities if c.get("change_pct") is not None and abs(c["change_pct"]) >= 1.0]
    if status == "normal":
        base = f"Vessel traffic at {display_name} is within its normal range."
    else:
        direction = "below" if deviation_pct < 0 else "above"
        base = f"Vessel traffic at {display_name} is {abs(deviation_pct):.0f}% {direction} its 14-day baseline ({status})."

    if moved:
        commodity_bits = ", ".join(f"{c['name']} {'up' if c['change_pct'] > 0 else 'down'} {abs(c['change_pct']):.1f}%" for c in moved)
        base += f" Related commodities moving: {commodity_bits}."
    return base


def get_supply_chain_correlation() -> Dict[str, Any]:
    commodities_data = commodity_prices.get_commodities()
    commodities_by_symbol = {c["symbol"]: c for c in commodities_data.get("commodities", [])}

    chokepoints_out = []
    for key, bbox_pair in CHOKEPOINTS.items():
        (min_lat, min_lon), (max_lat, max_lon) = bbox_pair
        current_count = len(vessel_store.query(bbox=(min_lat, min_lon, max_lat, max_lon)))
        baseline = timeseries_store.get_chokepoint_baseline(key, current_count)
        display_name = CHOKEPOINT_DISPLAY[key]

        related = []
        for symbol in CHOKEPOINT_COMMODITIES.get(key, []):
            c = commodities_by_symbol.get(symbol)
            if c:
                related.append({"symbol": symbol, "name": c["name"], "change_pct": c.get("change_pct")})

        chokepoints_out.append({
            "key": key,
            "name": display_name,
            **baseline,
            "related_commodities": related,
            "narrative": _narrative(display_name, baseline["status"], baseline.get("deviation_pct"), related),
        })

    return {
        "chokepoints": chokepoints_out,
        "commodities_last_error": commodities_data.get("last_error"),
    }
