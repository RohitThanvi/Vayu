"""universe.py — which companies and indices Vayu knows about, and how to find one.

Sources, in the order they are tried by `search()`:
  1. The S&P 500 constituent list shipped in app/data/universe_sp500.csv (503 rows incl. CIK and GICS sector). It comes from the
     open `datasets/s-and-p-500-companies` project, which in turn is built from Wikipedia's "List of S&P 500 companies"
     (CC BY-SA). It is a SNAPSHOT: refresh the CSV when index membership changes.
  2. Vayu's own curated indices, sector indices, FX and bellwether stocks (market_indices.py).
  3. Yahoo's keyless symbol search (unofficial, not live-tested from the build sandbox) for everything else — this is what
     makes the scope effectively "any listed symbol", including NSE/BSE names, without maintaining giant lists by hand.
A failure in (3) never breaks (1) and (2).
"""
import csv
import logging
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from .commodity_prices import _HEADERS
from . import market_indices

logger = logging.getLogger(__name__)

CSV_PATH = Path(__file__).resolve().parents[2] / "data" / "universe_sp500.csv"
YAHOO_SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
SEARCH_TTL_S = 3600
MAX_SEARCH_CACHE = 300
MAX_QUERY_LEN = 40
_NAME_NOISE = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "llc", "lp", "holdings", "holding",
               "group", "the", "sa", "nv", "ag", "se", "class", "a", "b", "c", "common", "stock"}

_lock = threading.Lock()
_universe: Optional[List[Dict[str, Any]]] = None
_search_cache: "OrderedDict[str, tuple]" = OrderedDict()


def yahoo_symbol(csv_symbol: str) -> str:
    """The S&P list writes BRK.B / BF.B; Yahoo spells them BRK-B / BF-B."""
    return csv_symbol.strip().upper().replace(".", "-")


def normalize_name(name: str) -> str:
    """Lower-case, punctuation-free, corporate-suffix-free form, for conservative exact-name matching."""
    words = re.sub(r"[^a-z0-9& ]+", " ", (name or "").lower().replace("&", " & ")).split()
    return " ".join(w for w in words if w not in _NAME_NOISE)


def load() -> List[Dict[str, Any]]:
    global _universe
    with _lock:
        if _universe is not None:
            return _universe
        rows: List[Dict[str, Any]] = []
        try:
            with open(CSV_PATH, newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    sym = yahoo_symbol(r.get("Symbol", ""))
                    if not sym:
                        continue
                    rows.append({"symbol": sym, "name": (r.get("Security") or "").strip(), "kind": "equity", "sector": r.get("GICS Sector") or None,
                                 "industry": r.get("GICS Sub-Industry") or None, "cik": int(r["CIK"]) if (r.get("CIK") or "").isdigit() else None,
                                 "source": "S&P 500", "exchange": None})
        except Exception as e:
            logger.warning(f"universe CSV could not be read: {type(e).__name__}: {e}")
        seen = {r["symbol"] for r in rows}
        for _group, items in market_indices.GROUPS:
            for sym, label, _unit, category in items:
                if sym in seen:
                    continue
                seen.add(sym)
                kind = "equity" if category in ("energy", "shipping", "logistics", "defense") else "fx" if category == "fx" else "index"
                rows.append({"symbol": sym, "name": label, "kind": kind, "sector": category, "industry": None, "cik": None, "source": "Vayu curated", "exchange": None})
        for r in rows:
            r["_norm"] = normalize_name(r["name"])
        _universe = rows
        return rows


def reset_for_tests() -> None:
    global _universe
    with _lock:
        _universe = None
        _search_cache.clear()


def _public(r: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in r.items() if not k.startswith("_")}


def by_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    s = (symbol or "").strip().upper()
    return next((r for r in load() if r["symbol"] == s), None)


def by_name(name: str) -> Optional[Dict[str, Any]]:
    """Exact match on the normalised name only (so "Walmart Inc." finds WMT, but "Apple Hospitality" never finds AAPL)."""
    n = normalize_name(name)
    if not n:
        return None
    hits = [r for r in load() if r["_norm"] == n]
    return hits[0] if len(hits) == 1 else None


def by_cik(cik: int) -> Optional[Dict[str, Any]]:
    return next((r for r in load() if r.get("cik") == cik), None)


def search_local(q: str, limit: int = 10) -> List[Dict[str, Any]]:
    ql = (q or "").strip().lower()[:MAX_QUERY_LEN]
    if not ql:
        return []
    qn = normalize_name(ql)
    scored = []
    for r in load():
        sym, name = r["symbol"].lower(), r["name"].lower()
        if sym == ql:
            rank = 0
        elif sym.startswith(ql):
            rank = 1
        elif name.startswith(ql) or (qn and r["_norm"].startswith(qn)):
            rank = 2
        elif ql in name or (qn and qn in r["_norm"]):
            rank = 3
        else:
            continue
        scored.append((rank, len(r["symbol"]), r["symbol"], r))
    scored.sort(key=lambda t: t[:3])
    return [_public(t[3]) for t in scored[:limit]]


def parse_yahoo_search(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for qd in (data or {}).get("quotes", []) or []:
        sym = qd.get("symbol")
        if not isinstance(sym, str) or not re.match(r"^[A-Za-z0-9.^=\-]{1,15}$", sym):
            continue
        qt = str(qd.get("quoteType", "")).upper()
        if qt not in ("EQUITY", "ETF", "INDEX", "CURRENCY", "MUTUALFUND", "FUTURE"):
            continue
        out.append({"symbol": sym.upper(), "name": qd.get("longname") or qd.get("shortname") or sym, "kind": {"EQUITY": "equity", "ETF": "etf", "INDEX": "index", "CURRENCY": "fx"}.get(qt, qt.lower()),
                    "sector": qd.get("sectorDisp") or qd.get("sector"), "industry": None, "cik": None, "source": "Yahoo search", "exchange": qd.get("exchDisp") or qd.get("exchange")})
    return out


async def search_yahoo(q: str, limit: int = 8) -> List[Dict[str, Any]]:
    try:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=8) as client:
            resp = await client.get(YAHOO_SEARCH_URL, params={"q": q, "quotesCount": limit, "newsCount": 0, "listsCount": 0})
            resp.raise_for_status()
            return parse_yahoo_search(resp.json())[:limit]
    except Exception as e:
        logger.warning(f"Yahoo symbol search failed for '{q}': {type(e).__name__}")
        return []


async def search(q: str, limit: int = 12) -> Dict[str, Any]:
    """Local matches first (instant, offline), then Yahoo's for anything not already listed. Never raises."""
    q = (q or "").strip()[:MAX_QUERY_LEN]
    if len(q) < 1:
        return {"query": q, "results": [], "remote_ok": True}
    key = q.lower()
    with _lock:
        hit = _search_cache.get(key)
        if hit and time.time() - hit[0] < SEARCH_TTL_S:
            return hit[1]
    local = search_local(q, limit)
    remote_ok = True
    results = list(local)
    if len(local) < limit:
        remote = await search_yahoo(q, limit)
        remote_ok = bool(remote) or len(q) < 2
        have = {r["symbol"] for r in results}
        results += [r for r in remote if r["symbol"] not in have]
    out = {"query": q, "results": results[:limit], "remote_ok": remote_ok}
    if remote_ok:                                   # never cache a transient upstream failure for an hour
        with _lock:
            _search_cache[key] = (time.time(), out)
            while len(_search_cache) > MAX_SEARCH_CACHE:
                _search_cache.popitem(last=False)
    return out
