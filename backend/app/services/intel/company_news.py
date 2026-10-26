"""company_news.py — the latest news mentions and the latest official filings for a company, with no paid search API.

NEWS: GDELT DOC 2.0 (https://api.gdeltproject.org/api/v2/doc/doc) — keyless, no monthly quota, "unlimited and unrestricted use" terms. Its one
limit is pacing: about one request every 5 seconds per IP (it answers HTTP 429 with a plain-text note otherwise). So every request goes through
one global pacer, results are cached 30 minutes per query, and the last good answer is served (marked stale) if GDELT is throttling or down.
Response shape used: {"articles": [{"title", "url", "domain", "sourcecountry", "language", "seendate": "YYYYMMDDTHHMMSSZ"}]}.
A GDELT hit means the company is MENTIONED in the article; it can be a passing mention, and headlines belong to their publishers.

FILINGS: the SEC submissions API (official, keyless, public domain) — the latest 8-K / 10-Q / 10-K etc. for US filers, with plain-English labels for
the standard Form 8-K item numbers. Non-US companies have none here.
Nothing in this module estimates, summarises or scores anything.
"""
import asyncio
import datetime as dt
import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

import httpx

from . import universe
from .commodity_prices import _HEADERS
from .company_graph import SUBMISSIONS_URL, ARCHIVE_URL, _get_json
from .sec_company import cik_for_symbol

logger = logging.getLogger(__name__)

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_MIN_GAP_S = 5.5
MAX_QUEUE_WAIT_S = 25.0
NEWS_TTL_S = 30 * 60
STALE_OK_S = 24 * 3600
FILINGS_TTL_S = 30 * 60
MAX_CACHE = 200
MAX_ARTICLES = 20
FETCH_RECORDS = 75
TIMESPANS = ("3d", "7d", "30d")
MAX_NAME_LEN = 80

_now = time.monotonic
_sleep = asyncio.sleep
_lock = threading.Lock()
_news_cache: "OrderedDict[str, Tuple[float, Dict[str, Any]]]" = OrderedDict()
_filings_cache: "OrderedDict[str, Tuple[float, Dict[str, Any]]]" = OrderedDict()
_pace = {"last": 0.0, "waiting": 0, "lock": None, "loop": None}


class RateLimited(Exception):
    pass


class Busy(Exception):
    pass


class BadResponse(Exception):
    pass


# ── query ────────────────────────────────────────────────────────────────────
_SUFFIX = re.compile(r"[\s,]+(?:inc|incorporated|corp|corporation|co|company|ltd|limited|plc|llc|lp|n\.?v|s\.?a|ag|se|holdings?)\.?$", re.I)
_PAREN = re.compile(r"\s*\([^)]*\)")


def clean_company_name(name: str) -> str:
    n = _PAREN.sub("", name or "").replace('"', " ").strip(" ,.")
    for _ in range(2):
        n2 = _SUFFIX.sub("", n).strip(" ,.")
        if n2 == n or len(n2) < 2:
            break
        n = n2
    return " ".join(n.split())[:MAX_NAME_LEN]


def build_query(name: str) -> Optional[str]:
    """Quoted company name; a single short word ("Apple", "Visa") also needs finance context to avoid unrelated mentions."""
    n = clean_company_name(name)
    if len(n) < 2:
        return None
    q = f'"{n}"'
    if " " not in n and len(n) <= 10:
        q += " (stock OR shares OR earnings OR company)"
    return q


def resolve_name(symbol: str, name: Optional[str]) -> Optional[str]:
    row = universe.by_symbol(symbol)
    chosen = (row or {}).get("name") or name
    return (chosen or "").strip()[:MAX_NAME_LEN] or None


# ── pacing ───────────────────────────────────────────────────────────────────
async def _paced():
    """Wait for this caller's turn: one GDELT request per GDELT_MIN_GAP_S across the whole server. Raises Busy if the queue is too long."""
    loop = asyncio.get_running_loop()
    if _pace["lock"] is None or _pace["loop"] is not loop:
        _pace.update(lock=asyncio.Lock(), loop=loop, waiting=0)
    if (_pace["waiting"] + 1) * GDELT_MIN_GAP_S > MAX_QUEUE_WAIT_S + GDELT_MIN_GAP_S:
        raise Busy()
    _pace["waiting"] += 1
    try:
        async with _pace["lock"]:
            wait = _pace["last"] + GDELT_MIN_GAP_S - _now()
            if wait > 0:
                await _sleep(wait)
            _pace["last"] = _now()
    finally:
        _pace["waiting"] -= 1


async def _gdelt(query: str, timespan: str) -> List[Dict[str, Any]]:
    await _paced()
    params = {"query": query, "mode": "ArtList", "format": "json", "sort": "DateDesc", "timespan": timespan, "maxrecords": FETCH_RECORDS}
    async with httpx.AsyncClient(headers=_HEADERS, timeout=25) as client:
        resp = await client.get(GDELT_URL, params=params)
    if resp.status_code == 429 or "limit requests" in resp.text[:200].lower():
        raise RateLimited()
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError:
        raise BadResponse(resp.text[:120])           # GDELT answers a bad query with plain text, not JSON
    return (data or {}).get("articles") or []


# ── parsing ──────────────────────────────────────────────────────────────────
def _seen_iso(s: Any) -> Optional[str]:
    try:
        return dt.datetime.strptime(str(s), "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _title_key(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()[:90]


def parse_articles(raw: List[Dict[str, Any]], limit: int = MAX_ARTICLES, english_only: bool = True) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for a in raw or []:
        if not isinstance(a, dict):
            continue                                   # a null/garbage entry must never take the whole list down
        title = " ".join(str(a.get("title") or "").split())[:200]
        url = a.get("url")
        if not title or not isinstance(url, str) or not url.startswith("https://"):
            continue                                   # http links, scripts and junk are dropped
        if english_only and str(a.get("language") or "").lower() not in ("english", "eng", "en", ""):
            continue
        key = _title_key(title)
        if not key or key in seen:
            continue                                   # the same wire story syndicated across many domains
        seen.add(key)
        out.append({"title": title, "url": url, "domain": str(a.get("domain") or "")[:60], "country": str(a.get("sourcecountry") or "")[:40] or None,
                    "seen": _seen_iso(a.get("seendate"))})
        if len(out) >= limit:
            break
    return sorted(out, key=lambda x: x["seen"] or "", reverse=True)


# ── news ─────────────────────────────────────────────────────────────────────
SOURCE = {"name": "GDELT DOC 2.0", "url": "https://www.gdeltproject.org/", "note": "Free and open news index. A hit means the company is mentioned in the article — "
          "possibly in passing. Headlines belong to their publishers; Vayu does not summarise or score them."}


def _put(cache: "OrderedDict", key: str, value: Dict[str, Any]) -> None:
    with _lock:
        cache[key] = (time.time(), value)
        cache.move_to_end(key)
        while len(cache) > MAX_CACHE:
            cache.popitem(last=False)


async def news(symbol: str, name: Optional[str] = None, timespan: str = "7d") -> Dict[str, Any]:
    sym = (symbol or "").strip().upper()
    nm = resolve_name(sym, name)
    q = build_query(nm) if nm else None
    base = {"symbol": sym, "company": clean_company_name(nm) if nm else None, "query": q, "timespan": timespan, "articles": [], "stale": False, "source": SOURCE, "notes": []}
    if timespan not in TIMESPANS:
        raise ValueError("timespan must be one of 3d, 7d, 30d.")
    if not q:
        base["notes"].append("No company name is known for this symbol, so news cannot be searched.")
        return base
    key = f"{q}|{timespan}"
    with _lock:
        hit = _news_cache.get(key)
    if hit and time.time() - hit[0] < NEWS_TTL_S:
        return hit[1]
    problem = None
    for attempt in (1, 2):
        try:
            raw = await _gdelt(q, timespan)
            res = {**base, "articles": parse_articles(raw), "fetched": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
            if not res["articles"]:
                res["notes"].append(f"No English-language articles mentioning {res['company']} in the last {timespan}.")
            _put(_news_cache, key, res)
            return res
        except RateLimited:
            problem = "The news source is throttling requests right now."
            if attempt == 1:
                await _sleep(6)
                continue
        except Busy:
            problem = "Many news lookups are queued right now."
            break
        except BadResponse as e:
            logger.warning(f"GDELT rejected query {q!r}: {e}")
            problem = "The news source could not parse this company name."
            break
        except Exception as e:
            logger.warning(f"GDELT request failed ({type(e).__name__})")
            problem = "The news source could not be reached."
            break
    if hit and time.time() - hit[0] < STALE_OK_S:
        return {**hit[1], "stale": True, "notes": [f"{problem} Showing the last result from {hit[1].get('fetched', 'earlier')}."]}
    base["notes"].append(f"{problem} Try again in a minute.")
    return base


# ── filings (SEC) ────────────────────────────────────────────────────────────
ITEM_LABELS = {
    "1.01": "Entry into a material agreement", "1.02": "Termination of a material agreement", "1.03": "Bankruptcy or receivership", "1.05": "Material cybersecurity incident",
    "2.01": "Acquisition or disposition of assets completed", "2.02": "Results of operations (earnings)", "2.03": "New direct financial obligation", "2.05": "Exit or disposal costs",
    "2.06": "Material impairment", "3.01": "Notice of delisting", "4.01": "Change of auditor", "4.02": "Non-reliance on previously issued financial statements",
    "5.01": "Change in control", "5.02": "Director or officer departure / appointment", "5.03": "Amendment of articles or bylaws", "5.07": "Shareholder vote results",
    "7.01": "Regulation FD disclosure", "8.01": "Other events", "9.01": "Financial statements and exhibits"}
FORM_LABELS = {"10-K": "Annual report", "10-K/A": "Annual report (amended)", "10-Q": "Quarterly report", "8-K": "Current report", "8-K/A": "Current report (amended)",
               "20-F": "Annual report (foreign issuer)", "6-K": "Report of foreign issuer", "DEF 14A": "Proxy statement", "S-1": "Registration statement"}


def parse_filings(sub: Dict[str, Any], cik: int, limit: int = 12) -> List[Dict[str, Any]]:
    rec = ((sub or {}).get("filings") or {}).get("recent") or {}
    cols = {k: rec.get(k) or [] for k in ("form", "filingDate", "accessionNumber", "primaryDocument", "items")}
    out = []
    for i, form in enumerate(cols["form"]):
        if form not in FORM_LABELS or i >= len(cols["accessionNumber"]) or i >= len(cols["filingDate"]):
            continue
        acc, doc = cols["accessionNumber"][i], cols["primaryDocument"][i] if i < len(cols["primaryDocument"]) else ""
        raw_items = cols["items"][i] if i < len(cols["items"]) else ""
        codes = [c.strip() for c in (raw_items if isinstance(raw_items, list) else str(raw_items or "").split(",")) if c.strip()]
        out.append({"form": form, "label": FORM_LABELS[form], "filed": cols["filingDate"][i], "accession": acc,
                    "items": [{"code": c, "label": ITEM_LABELS.get(c, "Item " + c)} for c in codes if c != "9.01" or len(codes) == 1][:6],
                    "url": ARCHIVE_URL.format(cik=cik, acc=acc.replace("-", ""), doc=doc) if doc else None})
        if len(out) >= limit:
            break
    return out


async def filings(symbol: str) -> Dict[str, Any]:
    sym = (symbol or "").strip().upper()
    out = {"symbol": sym, "filings": [], "available": False, "source": {"name": "SEC EDGAR", "url": "https://www.sec.gov/edgar/search/", "note": "Official filings, newest first."}, "notes": []}
    cik = await cik_for_symbol(sym)
    if not cik:
        out["notes"].append("No SEC filer is known for this symbol; non-US listings and indices have no SEC filings here.")
        return out
    with _lock:
        hit = _filings_cache.get(sym)
    if hit and time.time() - hit[0] < FILINGS_TTL_S:
        return hit[1]
    try:
        sub = await _get_json(SUBMISSIONS_URL.format(cik=cik))
    except httpx.HTTPStatusError as e:
        out["notes"].append("The SEC refused the request — set SEC_USER_AGENT to a real contact address." if e.response.status_code in (403, 429) else "The SEC returned an error.")
        return out
    except Exception as e:
        logger.warning(f"SEC submissions failed for CIK {cik}: {type(e).__name__}")
        out["notes"].append("The SEC could not be reached right now. Try again shortly.")
        return out
    out.update(available=True, cik=cik, entity=(sub or {}).get("name"), filings=parse_filings(sub, cik))
    if not out["filings"]:
        out["notes"].append("No recent annual, quarterly or current reports were found.")
    _put(_filings_cache, sym, out)
    return out
