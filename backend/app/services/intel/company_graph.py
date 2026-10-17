"""company_graph.py — a Bloomberg-SPLC-style "who sells to whom" view built ONLY from what companies themselves tell the SEC.

There is no free, complete supplier/customer database. What *is* public and legally required: a 10-K must name (or at least
count) customers that are ≥10% of revenue. Vayu reads that text:

  RIGHT side  (customers)   — the centre company's own latest 10-K: sentences like "<Name> accounted for 25% of net sales".
  LEFT side   (suppliers)   — other companies' 10-Ks (found with SEC full-text search on the centre company's name) where that
                              name sits right next to customer + %-of-revenue language, i.e. "we sell to <Centre> (14% of sales)".
Each edge keeps its evidence sentence, percentage, filing date and a link to the filing, and is labelled "extracted from filing
text — verify". Absence of an edge means "not found in the filings checked", never "no relationship". Private customers,
non-US companies and sub-10% relationships are mostly invisible here, and anonymised customers ("Customer A") are shown as such.
"""
import asyncio
import html
import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

import httpx

from . import universe
from .chokepoint_market_correlation import CHOKEPOINT_BELLWETHERS
from .edgar_exposure import EDGAR_SEARCH_URL
from .sec_company import headers, cik_for_symbol
from .supply_chain import CHOKEPOINT_DISPLAY

logger = logging.getLogger(__name__)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
MAX_DOC_BYTES = 6_000_000
MAX_FILERS = 8
CONCURRENCY = 3
BUDGET_S = 50.0
CACHE_TTL_S = 24 * 3600
MAX_CACHE = 60
SOURCE_LABEL = "Extracted from SEC filing text — verify in the filing"

_lock = threading.Lock()
_cache: "OrderedDict[str, Tuple[float, Dict[str, Any]]]" = OrderedDict()

# ── text handling ─────────────────────────────────────────────────────────────
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_DROP_BLOCKS = re.compile(r"(?is)<(script|style|ix:header)[^>]*>.*?</\1>")


def clean_html(raw: str) -> str:
    """Filing HTML (incl. inline XBRL) -> plain text with single spaces."""
    t = _DROP_BLOCKS.sub(" ", raw or "")
    t = _TAGS.sub(" ", t)
    return _WS.sub(" ", html.unescape(t).replace("\xa0", " ")).strip()


def snippet(text: str, start: int, end: int, words: int = 40) -> str:
    left = text[max(0, start - 220):start].split()
    right = text[end:end + 220].split()
    half = words // 2
    core = text[start:end].strip()
    out = " ".join(left[-half:] + [core] + right[:half])
    return out[:420]


_PCT = r"(\d{1,2}(?:\.\d+)?)\s?%"
_OF_REV = r"of\s+(?:our\s+|the\s+company['’]s\s+|its\s+|total\s+|consolidated\s+|net\s+|annual\s+|worldwide\s+|global\s+)*(?:net\s+)?(?:sales|revenues?)"
_NAME = r"[A-Z][\w&.'’\-]*(?:\s+(?:[A-Z][\w&.'’\-]*|of|and|&|de|the)){0,5}"
_CONC = re.compile(
    rf"(?P<name>{_NAME})(?:\s*\([^)]{{0,50}}\))?(?:\s*,[^,.;]{{0,70}},)?[\s,]+(?:accounted\s+for|represented|represents|comprised|contributed|was|were)\s+"
    rf"(?:approximately\s+|about\s+|over\s+|more\s+than\s+)?{_PCT}\s+{_OF_REV}")
# Segment / product / region labels that read like names ("Automotive accounted for 80% of revenues") but are not customers.
_GENERIC = {"automotive", "services", "service", "products", "product", "software", "hardware", "retail", "wholesale", "consumer", "commercial", "industrial",
            "enterprise", "government", "healthcare", "domestic", "international", "americas", "europe", "asia", "pacific", "emea", "apac", "china", "india",
            "japan", "canada", "mexico", "licensing", "subscription", "subscriptions", "advertising", "royalties", "maintenance", "energy", "medical", "online",
            "direct", "indirect", "other", "others", "north", "south", "east", "west", "united", "states", "america", "segment", "segments", "goods", "equipment"}
_ANON = re.compile(r"^(customer|distributor|reseller|retailer|partner|client|licensee|oem)\s+[a-z0-9]{1,2}$", re.I)
_BAD_NAME = re.compile(r"^(no|one|two|three|a|an|the|our|each|any|sales|revenues?|net|total|in|for|of|to|and|company|we|it|segment|region)\b", re.I)


def extract_concentration(text: str) -> List[Dict[str, Any]]:
    """Customers named (or anonymised) with a %-of-revenue figure, each with its evidence sentence. A match counts only
    if 'customer' appears within 250 characters, which screens out segment/product splits ("Automotive accounted for 80%")."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for m in _CONC.finditer(text or ""):
        name = m.group("name").strip(" ,;")
        if name.endswith(".") and not re.search(r"\b(?:Inc|Corp|Co|Ltd|Cos|LLC|Bros|Mfg|Intl)\.$", name):
            name = name.rstrip(".")                                         # keep "Inc." / "Corp.", drop a sentence-ending period
        win = text[max(0, m.start() - 250): m.end() + 250].lower()
        if "customer" not in win and "distributor" not in win:
            continue
        anon = bool(_ANON.match(name))
        if not anon and (_BAD_NAME.match(name) or len(name) < 3):
            continue
        if not anon and all(w.lower().strip(".,'’") in _GENERIC for w in name.split()):
            continue
        key = (name.lower(), m.group(2))
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "pct": float(m.group(2)), "anonymous": anon, "snippet": snippet(text, m.start(), m.end())})
    return out


_ABBR = re.compile(r"(?:\s+(?:Company|Corporation|Corp\.?|Inc\.?|Co\.?|Ltd\.?|plc|N\.V\.))?\s*\(\s*(?:the\s+)?[“\"‘']*(?:or\s+)?[“\"‘']*([A-Z][A-Za-z0-9&.\-]{1,10})[”\"’']*\s*\)")


def alias_forms(names: List[str]) -> List[str]:
    forms = []
    for n in names:
        n = (n or "").strip()
        if len(n) >= 4 and n.lower() not in [f.lower() for f in forms]:
            forms.append(n)
        nn = universe.normalize_name(n)
        if len(nn) >= 4 and nn not in [f.lower() for f in forms]:
            forms.append(nn)
    return forms


_BOUNDARY = re.compile(r"(?<=[a-z0-9%)\]])\.\s+(?=[A-Z])")


def _sentence_span(text: str, start: int, end: int, reach: int = 300) -> Tuple[int, int]:
    """Rough sentence around [start, end): a period after a word/number followed by a capital starts a new sentence
    ("Inc. accounted" does not, because the next word is lower-case)."""
    left = text[max(0, start - reach):start]
    cuts = list(_BOUNDARY.finditer(left))
    a = max(0, start - reach) + (cuts[-1].end() if cuts else 0)
    right = text[end:end + reach]
    m = _BOUNDARY.search(right)
    return a, end + (m.start() + 1 if m else len(right))


def find_mentions(text: str, aliases: List[str]) -> Optional[Dict[str, Any]]:
    """Best place in `text` where the company is named right beside customer + %-of-revenue language (a supplier saying
    'X accounted for 14% of our net sales'). Abbreviations the filing itself defines — Name ("GM") — are followed too."""
    forms = list(aliases)
    pats = [re.compile(r"(?<![A-Za-z])" + re.escape(a) + r"(?![A-Za-z])", re.I) for a in forms]
    for p in list(pats):                                      # abbreviations the filing defines right after the full name
        for m in p.finditer(text):
            ab = _ABBR.match(text, m.end())
            if ab and len(ab.group(1)) >= 2:
                pats.append(re.compile(r"(?<![A-Za-z0-9])" + re.escape(ab.group(1)) + r"(?![A-Za-z0-9])"))
                break
    best = None
    for p in pats:
        for m in p.finditer(text):
            if "customer" not in text[max(0, m.start() - 300): m.end() + 300].lower():
                continue
            sa, sb = _sentence_span(text, m.start(), m.end())
            sent = text[sa:sb]                                         # the revenue share must be in the SAME sentence as the name
            off = sa
            pcts = [(abs((off + pm.start()) - m.start()), float(pm.group(1)), pm) for pm in re.finditer(_PCT + r"\s+" + _OF_REV, sent, re.I)]
            if not pcts and re.search(r"accounted\s+for|represented", sent, re.I):
                pcts = [(999, None, None)]
            if not pcts:
                continue
            dist, pct, _ = min(pcts, key=lambda t: t[0])
            if best is None or dist < best["_d"]:
                best = {"pct": pct, "snippet": snippet(text, m.start(), m.end()), "_d": dist}
    if best:
        best.pop("_d")
    return best


# ── SEC plumbing (everything that touches the network is in these small functions so tests can replace them) ──
async def fetch_text(url: str) -> Optional[str]:
    try:
        buf = bytearray()
        async with httpx.AsyncClient(headers=headers(), timeout=30, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    buf += chunk
                    if len(buf) > MAX_DOC_BYTES:
                        break
        return clean_html(bytes(buf).decode("utf-8", errors="ignore"))
    except Exception as e:
        logger.warning(f"SEC document fetch failed ({type(e).__name__}) for {url}")
        return None


async def _get_json(url: str, params: Optional[dict] = None) -> Any:
    async with httpx.AsyncClient(headers=headers(), timeout=25, follow_redirects=True) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


async def latest_10k(cik: int) -> Optional[Dict[str, Any]]:
    try:
        sub = await _get_json(SUBMISSIONS_URL.format(cik=cik))
    except Exception as e:
        logger.warning(f"SEC submissions failed for CIK {cik}: {type(e).__name__}")
        return None
    rec = ((sub or {}).get("filings") or {}).get("recent") or {}
    forms, accs, docs, dates = (rec.get(k) or [] for k in ("form", "accessionNumber", "primaryDocument", "filingDate"))
    for i, f in enumerate(forms):
        if f == "10-K" and i < len(accs) and i < len(docs) and docs[i]:
            return {"cik": cik, "accession": accs[i], "filed": dates[i] if i < len(dates) else None, "entity": (sub or {}).get("name"),
                    "url": ARCHIVE_URL.format(cik=cik, acc=accs[i].replace("-", ""), doc=docs[i])}
    return {"cik": cik, "entity": (sub or {}).get("name"), "url": None}


async def efts_search(name: str, start: str, end: str) -> Any:
    return await _get_json(EDGAR_SEARCH_URL, {"q": f'"{name}"', "forms": "10-K", "startdt": start, "enddt": end})


_TICKER_PAREN = re.compile(r"\(([A-Z][A-Z.\-]{0,5}(?:,\s*[A-Z][A-Z.\-]{0,5})*)\)")


def parse_efts_hits(data: Any, exclude_cik: Optional[int], limit: int = MAX_FILERS) -> List[Dict[str, Any]]:
    """One candidate filing per filer from an EDGAR full-text-search response (best-ranked first)."""
    out, seen = [], set()
    for hit in ((data or {}).get("hits") or {}).get("hits") or []:
        src = hit.get("_source") or {}
        _id = str(hit.get("_id") or "")
        acc, _, fname = _id.partition(":")
        acc = src.get("adsh") or acc
        ciks = src.get("ciks") or []
        try:
            cik = int(ciks[0])
        except (IndexError, ValueError, TypeError):
            continue
        if not acc or not fname or cik == exclude_cik or cik in seen:
            continue
        seen.add(cik)
        disp = (src.get("display_names") or [""])[0]
        tick = _TICKER_PAREN.search(disp)
        name = re.sub(r"\s*\((?:[A-Z][A-Z.\-]{0,5}(?:,\s*[A-Z][A-Z.\-]{0,5})*|CIK[^)]*)\)", "", disp).strip() or "Unknown filer"
        out.append({"cik": cik, "name": name, "ticker": tick.group(1).split(",")[0].strip() if tick else None, "accession": acc, "filed": src.get("file_date"),
                    "url": ARCHIVE_URL.format(cik=cik, acc=acc.replace("-", ""), doc=fname)})
        if len(out) >= limit:
            break
    return out


# ── assembling the graph ──────────────────────────────────────────────────────
def _symbol_for(name: Optional[str], cik: Optional[int], ticker: Optional[str]) -> Optional[str]:
    row = (universe.by_cik(cik) if cik else None) or (universe.by_name(name) if name else None)
    if row:
        return row["symbol"]
    return ticker.replace(".", "-").upper() if ticker else None


def exposures_for(symbol: str) -> List[Dict[str, Any]]:
    return [{"chokepoint": cp, "display": CHOKEPOINT_DISPLAY.get(cp, cp), "basis": "Vayu's published bellwether mapping (not the company's disclosure)"}
            for cp, syms in CHOKEPOINT_BELLWETHERS.items() if symbol in syms]


def _edge_customer(e: Dict[str, Any], filing: Dict[str, Any]) -> Dict[str, Any]:
    sym = None if e["anonymous"] else _symbol_for(e["name"], None, None)
    return {"name": f"{e['name']} (undisclosed)" if e["anonymous"] else e["name"], "symbol": sym, "cik": None, "pct": e["pct"], "evidence": e["snippet"],
            "source_label": SOURCE_LABEL, "source_url": filing.get("url"), "filed": filing.get("filed"), "confidence": "extracted", "anonymous": e["anonymous"]}


async def build(symbol: str) -> Dict[str, Any]:
    sym = (symbol or "").strip().upper()
    row = universe.by_symbol(sym)
    cik = await cik_for_symbol(sym)
    center = {"symbol": sym, "name": (row or {}).get("name") or sym, "cik": cik, "sector": (row or {}).get("sector"), "industry": (row or {}).get("industry")}
    base = {"center": center, "suppliers": [], "customers": [], "exposures": exposures_for(sym), "partial": False, "notes": [
        "Built only from companies' own SEC filings. A missing relationship means it was not found in the filings checked — not that it does not exist.",
        "Only customers above the disclosure threshold (typically 10% of revenue) appear. Private companies, non-US companies and smaller relationships are mostly invisible.",
        "Every edge is extracted automatically from filing text. Open the filing to verify before relying on it."]}
    if not cik:
        base["notes"].insert(0, "No SEC filer is known for this symbol, so no supplier/customer data can be read (non-US listings and indices have none here).")
        return base
    deadline = time.monotonic() + BUDGET_S
    k = await latest_10k(cik)
    names = [center["name"]] + ([k["entity"]] if k and k.get("entity") else [])
    aliases = alias_forms(names)
    sem = asyncio.Semaphore(CONCURRENCY)

    async def customers() -> List[Dict[str, Any]]:
        if not k or not k.get("url"):
            return []
        async with sem:
            text = await fetch_text(k["url"])
        if text is None:
            base["partial"] = True
            return []
        return [_edge_customer(e, k) for e in sorted(extract_concentration(text), key=lambda x: -x["pct"])]

    async def suppliers() -> List[Dict[str, Any]]:
        import datetime as dt
        end = dt.date.today(); start = end - dt.timedelta(days=730)
        try:
            cands = parse_efts_hits(await efts_search(aliases[0], start.isoformat(), end.isoformat()), cik)
        except Exception as e:
            logger.warning(f"EDGAR full-text search failed for '{aliases[0]}': {type(e).__name__}")
            base["partial"] = True
            return []

        async def one(c: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            if time.monotonic() > deadline:
                base["partial"] = True
                return None
            async with sem:
                text = await fetch_text(c["url"])
            if text is None:
                base["partial"] = True
                return None
            m = find_mentions(text, aliases)
            if not m:
                return None
            return {"name": c["name"], "symbol": _symbol_for(c["name"], c["cik"], c["ticker"]), "cik": c["cik"], "pct": m["pct"], "evidence": m["snippet"], "source_label": SOURCE_LABEL,
                    "source_url": c["url"], "filed": c["filed"], "confidence": "extracted", "anonymous": False}
        found = [r for r in await asyncio.gather(*(one(c) for c in cands)) if r]
        return sorted(found, key=lambda r: -(r["pct"] or 0))

    cust, supp = await asyncio.gather(customers(), suppliers())
    base["customers"], base["suppliers"] = cust, supp
    if base["partial"]:
        base["notes"].insert(0, "Some SEC documents could not be read in time or at all, so this view may be incomplete. Try again later.")
    return base


async def relationships(symbol: str) -> Dict[str, Any]:
    key = (symbol or "").strip().upper()
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL_S:
            _cache.move_to_end(key)
            return hit[1]
    out = await build(key)
    if not out["partial"]:                       # never cache an incomplete read for a day
        with _lock:
            _cache[key] = (time.time(), out)
            while len(_cache) > MAX_CACHE:
                _cache.popitem(last=False)
    return out
