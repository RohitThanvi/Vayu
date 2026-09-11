"""
edgar_exposure.py — cross-references live chokepoint/commodity signals
(already tracked in supply_chain.py) against SEC EDGAR's free full-text
search API to surface which public companies have RECENTLY disclosed
exposure to the thing that's currently happening.

This is the actual point of "business intelligence" over a pile of
dashboards: instead of "vessel traffic at Hormuz is down 30%", this
answers "...and here are 6 companies that just told the SEC they're
exposed to Hormuz."

Data source: SEC EDGAR full-text search — https://www.sec.gov/edgar/
search/ — free, no API key, covers filings back to 2001. Official
guidance requires a descriptive User-Agent identifying the requester;
Vayu's is set below per https://www.sec.gov/os/webmaster-faq#developers.
"""

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
USER_AGENT = "Vayu Geospatial Intelligence contact@vayu-terminal.example (research/non-commercial EDGAR full-text search use)"

CHOKEPOINT_SEARCH_TERMS = {
    "strait_of_hormuz":    ["Strait of Hormuz", "Hormuz"],
    "strait_of_malacca":   ["Strait of Malacca", "Malacca Strait"],
    "bab_el_mandeb":       ["Bab el-Mandeb", "Bab-el-Mandeb"],
    "suez_canal":          ["Suez Canal"],
    "strait_of_gibraltar": ["Strait of Gibraltar"],
    "panama_canal":        ["Panama Canal"],
    "english_channel":     ["English Channel"],
}

_cache: dict[str, tuple[float, list[dict]]] = {}
CACHE_TTL_SECONDS = 3600  # EDGAR filings don't move fast enough to justify re-querying often


async def search_exposure(term: str, days_back: int = 14, limit: int = 10) -> list[dict]:
    """Full-text search recent 8-K/10-K/10-Q filings mentioning `term`.
    Returns filer name, form type, filing date, and a link to EDGAR's
    own search results for that filing (the full-text API doesn't
    reliably return a direct document URL, so we link to the search
    result rather than guess a URL and risk it being wrong)."""
    cache_key = f"{term}|{days_back}"
    now = time.time()
    if cache_key in _cache and now - _cache[cache_key][0] < CACHE_TTL_SECONDS:
        return _cache[cache_key][1]

    from datetime import datetime, timedelta
    start = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    end = datetime.utcnow().strftime("%Y-%m-%d")

    params = {
        "q": f'"{term}"',
        "forms": "8-K,10-K,10-Q",
        "startdt": start,
        "enddt": end,
    }
    try:
        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
            resp = await client.get(EDGAR_SEARCH_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"EDGAR full-text search failed for '{term}': {type(e).__name__}: {e}")
        return _cache.get(cache_key, (0, []))[1]  # serve stale cache rather than nothing, if we have it

    results = []
    for hit in data.get("hits", {}).get("hits", [])[:limit]:
        src = hit.get("_source", {})
        display_names = src.get("display_names", [])
        company = display_names[0].split(" (CIK")[0] if display_names else "Unknown filer"
        results.append({
            "company": company,
            "form_type": src.get("form", ""),
            "filed": src.get("file_date", ""),
            "search_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={company.replace(' ', '+')}&type=&dateb=&owner=include&count=40",
        })
    _cache[cache_key] = (now, results)
    return results


async def exposure_for_chokepoint(chokepoint_id: str, days_back: int = 14, limit: int = 8) -> dict[str, Any]:
    """Runs search_exposure across every alias for a chokepoint (e.g.
    both 'Strait of Hormuz' and 'Hormuz') and merges/dedupes by company
    + filing date."""
    terms = CHOKEPOINT_SEARCH_TERMS.get(chokepoint_id, [chokepoint_id])
    seen = set()
    merged: list[dict] = []
    for term in terms:
        hits = await search_exposure(term, days_back=days_back, limit=limit)
        for h in hits:
            key = (h["company"], h["filed"], h["form_type"])
            if key in seen:
                continue
            seen.add(key)
            merged.append(h)
    merged.sort(key=lambda h: h.get("filed", ""), reverse=True)
    return {
        "chokepoint": chokepoint_id,
        "search_terms": terms,
        "days_back": days_back,
        "filings": merged[:limit],
        "count": len(merged),
    }
