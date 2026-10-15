"""asset_profile.py — the "one page on this asset" a terminal user expects: click a ticker or a strategic site and see
everything Vayu knows about it in one place.

Descriptive only. Price statistics are computed from daily closes; the links to chokepoints come from mappings that are
already explicit in this codebase (chokepoint_market_correlation.CHOKEPOINT_BELLWETHERS and the sites' own notes), never
inferred here. There is no composite "score" and no forecast.
"""
import math
from typing import Any, Dict, List, Optional

from .chokepoint_market_correlation import CHOKEPOINT_BELLWETHERS
from .supply_chain import CHOKEPOINT_DISPLAY
from . import strategic_sites

# Only sites whose own `note` in strategic_sites.SITES names the chokepoint (Jebel Ali: "near Strait of Hormuz";
# Fujairah: "outside the Strait of Hormuz"; Singapore: "Key Malacca Strait transshipment hub"). No distance-based guessing.
SITE_CHOKEPOINT = {"jebel_ali": "strait_of_hormuz", "fujairah": "strait_of_hormuz", "singapore": "strait_of_malacca"}

TRADING_DAYS = {"1m": 21, "3m": 63, "1y": 252}


def _r(v: Optional[float], nd: int = 2) -> Optional[float]:
    return None if v is None else round(v, nd)


def price_stats(closes: List[float]) -> Dict[str, Any]:
    """Return windows, annualised volatility, max drawdown and position in the 52-week range, from daily closes.
    Needs at least 30 closes; otherwise every figure is None (the UI shows "not enough history", not a guess)."""
    closes = [float(c) for c in closes if c is not None and c > 0]
    n = len(closes)
    empty = {"n_days": n, "return_1m_pct": None, "return_3m_pct": None, "return_1y_pct": None,
             "volatility_annualised_pct": None, "max_drawdown_pct": None, "position_in_52w_range_pct": None,
             "low_52w": None, "high_52w": None}
    if n < 30:
        return empty

    def ret(k: int) -> Optional[float]:
        return (closes[-1] / closes[-1 - k] - 1) * 100 if n > k else None

    log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, n)]
    mean = sum(log_rets) / len(log_rets)
    var = sum((x - mean) ** 2 for x in log_rets) / (len(log_rets) - 1)
    peak, mdd = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        mdd = min(mdd, (c / peak - 1) * 100)
    window = closes[-TRADING_DAYS["1y"]:]
    lo, hi = min(window), max(window)
    return {
        "n_days": n,
        "return_1m_pct": _r(ret(TRADING_DAYS["1m"])), "return_3m_pct": _r(ret(TRADING_DAYS["3m"])), "return_1y_pct": _r(ret(TRADING_DAYS["1y"])),
        "volatility_annualised_pct": _r(math.sqrt(var) * math.sqrt(252) * 100),
        "max_drawdown_pct": _r(mdd),
        "position_in_52w_range_pct": _r((closes[-1] - lo) / (hi - lo) * 100) if hi > lo else None,
        "low_52w": _r(lo, 4), "high_52w": _r(hi, 4),
    }


def _linked_chokepoints(cp_ids: List[str], risk_by_id: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"chokepoint": cp, "display": CHOKEPOINT_DISPLAY.get(cp, cp),
             "band": (risk_by_id.get(cp) or {}).get("band"), "score": (risk_by_id.get(cp) or {}).get("score")} for cp in cp_ids]


def ticker_profile(symbol: str, quote: Optional[Dict[str, Any]], closes: List[float], risk_by_id: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    linked = _linked_chokepoints([cp for cp, syms in CHOKEPOINT_BELLWETHERS.items() if symbol in syms], risk_by_id)
    notes = ["Statistics are descriptive, computed from daily closes (1m ≈ 21, 3m ≈ 63, 1y ≈ 252 trading days). They are not a forecast."]
    notes.append("Chokepoint links come from Vayu's published bellwether mapping, not from the company's own disclosures."
                 if linked else "This symbol is not one of Vayu's chokepoint bellwethers, so no chokepoint link is shown.")
    return {"kind": "ticker", "id": symbol, "name": (quote or {}).get("name") or symbol, "quote": quote,
            "stats": price_stats(closes), "linked_chokepoints": linked, "notes": notes}


def site_profile(site: Dict[str, Any], usgs_events: List[dict], gdelt_events: List[dict], dark_flags: List[dict],
                 risk_by_id: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    cp = SITE_CHOKEPOINT.get(site["id"])
    bellwethers = CHOKEPOINT_BELLWETHERS.get(cp, []) if cp else []
    return {
        "kind": "site", "id": site["id"], "name": site["name"], "site_type": site["type"], "lat": site["lat"], "lon": site["lon"], "note": site.get("note"),
        "signals": strategic_sites.get_recent_signals(site, usgs_events, gdelt_events, dark_flags),
        "recent_news": strategic_sites.get_recent_news(site, gdelt_events),
        "linked_chokepoints": _linked_chokepoints([cp], risk_by_id) if cp else [],
        "related_tickers": bellwethers,
        "notes": ["Signals are only things detected near this site right now; an empty list means none detected, not that nothing is happening.",
                  "The chokepoint link exists only where the site's own description names one." if cp else "No chokepoint is named for this site, so none is linked."],
    }
