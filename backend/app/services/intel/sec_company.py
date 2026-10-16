"""sec_company.py — what a company has told the SEC, from the SEC's own free, keyless JSON APIs.

Two jobs:
  * financials(symbol): annual revenue / net income / operating income / assets / operating cash flow / diluted EPS from the
    XBRL "company facts" API (https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json), 10-K annual values only.
  * (company_graph.py builds the supplier/customer view from filing TEXT using the helpers added further down.)

Honest limits, repeated in every response's `notes`: US-GAAP filers only (most foreign companies, incl. most Indian ones, file
20-F or nothing here); values are exactly what the filer tagged, not restated or normalised by Vayu; nothing is estimated.
Requests carry SEC_USER_AGENT (the SEC requires a real contact address) and stay far below the SEC's 10 requests/second.
"""
import datetime as dt
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

import httpx

from . import universe
from .edgar_exposure import USER_AGENT

logger = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
TICKER_TTL_S = 24 * 3600
FACTS_TTL_S = 12 * 3600
MAX_CACHE = 120
YEARS = 8
ANNUAL_FORMS = ("10-K", "10-K/A", "10-KT")

# metric -> (candidate us-gaap tags, unit, is_flow). A company may switch tags over the years, so the candidate whose
# series reaches the most recent year wins (ties: more points).
METRICS: Dict[str, Tuple[List[str], str, bool]] = {
    "revenue": (["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"], "USD", True),
    "net_income": (["NetIncomeLoss", "ProfitLoss"], "USD", True),
    "operating_income": (["OperatingIncomeLoss"], "USD", True),
    "operating_cash_flow": (["NetCashProvidedByUsedInOperatingActivities"], "USD", True),
    "assets": (["Assets"], "USD", False),
    "eps_diluted": (["EarningsPerShareDiluted"], "USD/shares", True),
}

_lock = threading.Lock()
_ticker_map: Dict[str, Any] = {"at": 0.0, "map": {}}
_facts_cache: "OrderedDict[int, Tuple[float, Dict[str, Any]]]" = OrderedDict()


def headers() -> Dict[str, str]:
    return {"User-Agent": USER_AGENT, "Accept": "application/json"}


# ── ticker -> CIK ─────────────────────────────────────────────────────────────
def parse_ticker_map(data: Any) -> Dict[str, int]:
    """Accepts company_tickers.json ({"0": {"cik_str", "ticker", "title"}, ...}) or company_tickers_exchange.json
    ({"fields": [...], "data": [[cik, name, ticker, exchange], ...]})."""
    out: Dict[str, int] = {}
    if isinstance(data, dict) and isinstance(data.get("data"), list) and isinstance(data.get("fields"), list):
        f = data["fields"]
        try:
            ic, it = f.index("cik"), f.index("ticker")
        except ValueError:
            return out
        for row in data["data"]:
            try:
                out[str(row[it]).upper().replace(".", "-")] = int(row[ic])
            except (IndexError, ValueError, TypeError):
                continue
    elif isinstance(data, dict):
        for v in data.values():
            try:
                out[str(v["ticker"]).upper().replace(".", "-")] = int(v["cik_str"])
            except (KeyError, ValueError, TypeError):
                continue
    return out


async def _get_json(url: str, timeout: float = 20) -> Any:
    async with httpx.AsyncClient(headers=headers(), timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


async def cik_for_symbol(symbol: str) -> Optional[int]:
    sym = (symbol or "").strip().upper()
    row = universe.by_symbol(sym)
    if row and row.get("cik"):
        return int(row["cik"])
    if "." in sym and sym.rsplit(".", 1)[1] in ("NS", "BO", "L", "TO", "HK", "T", "AX", "SS", "SZ", "DE", "PA"):
        return None                                   # a non-US listing: the SEC ticker file would only mislead
    if sym.startswith("^") or "=" in sym:
        return None
    with _lock:
        fresh = time.time() - _ticker_map["at"] < TICKER_TTL_S and _ticker_map["map"]
        mp = _ticker_map["map"]
    if not fresh:
        try:
            mp = parse_ticker_map(await _get_json(TICKERS_URL))
            with _lock:
                _ticker_map.update(at=time.time(), map=mp)
        except Exception as e:
            logger.warning(f"SEC ticker map unavailable: {type(e).__name__}")
    return mp.get(sym.replace(".", "-"))


# ── financials ────────────────────────────────────────────────────────────────
def _days(a: str, b: str) -> Optional[int]:
    try:
        return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days
    except (ValueError, TypeError):
        return None


def annual_series(facts: Dict[str, Any], tags: List[str], unit: str, is_flow: bool, years: int = YEARS) -> List[Dict[str, Any]]:
    """Annual values for the best-covered tag: 10-K facts, flow items spanning ~1 year, one value per period end
    (the latest filing wins, so restatements replace originals)."""
    best: List[Dict[str, Any]] = []
    gaap = ((facts or {}).get("facts") or {}).get("us-gaap") or {}
    for tag in tags:
        entries = (((gaap.get(tag) or {}).get("units") or {}).get(unit)) or []
        by_end: Dict[str, Dict[str, Any]] = {}
        for e in entries:
            if e.get("form") not in ANNUAL_FORMS or e.get("fp") != "FY" or e.get("val") is None or not e.get("end"):
                continue
            if is_flow:
                d = _days(e.get("start", ""), e["end"])
                if d is None or not (340 <= d <= 380):
                    continue
            cur = by_end.get(e["end"])
            if cur is None or (e.get("filed") or "") > (cur.get("filed") or ""):
                by_end[e["end"]] = e
        pts = [{"end": k, "value": v["val"], "form": v["form"], "filed": v.get("filed"), "accession": v.get("accn")} for k, v in sorted(by_end.items())][-years:]
        if pts and (not best or (pts[-1]["end"], len(pts)) > (best[-1]["end"], len(best))):
            best = pts
    return best


def derive(series: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Year-by-year growth and margins using only values both reported in the same period."""
    rev = {p["end"]: p["value"] for p in series.get("revenue", [])}
    ni = {p["end"]: p["value"] for p in series.get("net_income", [])}
    oi = {p["end"]: p["value"] for p in series.get("operating_income", [])}
    ends = sorted(rev)
    growth = [{"end": e, "pct": round((rev[e] / rev[p] - 1) * 100, 1)} for p, e in zip(ends, ends[1:]) if rev[p] and rev[p] > 0]
    nm = [{"end": e, "pct": round(ni[e] / rev[e] * 100, 1)} for e in ends if e in ni and rev[e]]
    om = [{"end": e, "pct": round(oi[e] / rev[e] * 100, 1)} for e in ends if e in oi and rev[e]]
    return {"revenue_growth_pct": growth, "net_margin_pct": nm, "operating_margin_pct": om}


def build_financials(facts: Dict[str, Any], cik: int, symbol: str) -> Dict[str, Any]:
    series = {m: annual_series(facts, tags, unit, flow) for m, (tags, unit, flow) in METRICS.items()}
    series = {m: s for m, s in series.items() if s}
    notes = ["Figures are exactly what the company tagged in its annual 10-K filings (US GAAP, USD); Vayu does not restate, adjust or estimate them.",
             "Fiscal years end on different dates for different companies, so compare by period-end date, not by calendar year."]
    if not series:
        notes.insert(0, "No annual US-GAAP data was found for this company. Foreign private issuers (most Indian and other non-US companies) do not file 10-Ks.")
    return {"symbol": symbol, "cik": cik, "entity": (facts or {}).get("entityName"), "currency": "USD", "series": series, "derived": derive(series) if series else {},
            "source": "SEC EDGAR XBRL company facts", "source_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik:010d}&type=10-K",
            "as_of": max((p["filed"] for s in series.values() for p in s if p.get("filed")), default=None), "notes": notes}


async def financials(symbol: str) -> Dict[str, Any]:
    sym = (symbol or "").strip().upper()
    cik = await cik_for_symbol(sym)
    if not cik:
        return {"symbol": sym, "cik": None, "series": {}, "derived": {}, "available": False,
                "notes": ["No SEC filer is known for this symbol. The SEC covers US-listed companies; non-US listings (e.g. .NS/.BO) and indices have no 10-K data here."]}
    with _lock:
        hit = _facts_cache.get(cik)
        if hit and time.time() - hit[0] < FACTS_TTL_S:
            _facts_cache.move_to_end(cik)
            return hit[1]
    try:
        facts = await _get_json(FACTS_URL.format(cik=cik), timeout=30)
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        msg = ("The SEC refused the request — it requires a real contact address in the User-Agent. Set SEC_USER_AGENT on the server." if code in (403, 429)
               else "This company has no XBRL facts at the SEC." if code == 404 else "The SEC service returned an error.")
        logger.warning(f"SEC companyfacts HTTP {code} for CIK {cik}")
        return {"symbol": sym, "cik": cik, "series": {}, "derived": {}, "available": False, "notes": [msg]}
    except Exception as e:
        logger.warning(f"SEC companyfacts failed for CIK {cik}: {type(e).__name__}")
        return {"symbol": sym, "cik": cik, "series": {}, "derived": {}, "available": False, "notes": ["The SEC could not be reached right now. Try again shortly."]}
    out = {**build_financials(facts, cik, sym), "available": True}
    with _lock:
        _facts_cache[cik] = (time.time(), out)
        while len(_facts_cache) > MAX_CACHE:
            _facts_cache.popitem(last=False)
    return out
