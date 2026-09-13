"""
economic_blocs.py — G7/G20/BRICS/ASEAN analysis, built entirely on data
sources already integrated elsewhere in this project (World Bank,
GDELT, SEC EDGAR, commodity_prices) — no new API/key. Four things per
bloc:

  1. Macro rollup   — World Bank GDP growth + inflation for every
     member country. Same _world_bank_latest() macro.py already uses
     for the global/India figures, just called once per member.
  2. Regional tone   — GDELT events (already ingested by fetchers.py)
     filtered by whether the bloc's name or a member country's name
     appears in the headline, instead of geo_tone.py's lat/lon grid
     approach (a bloc isn't one place on a map, so geography doesn't
     apply — keyword matching does).
  3. Commodity relevance — which of the 10 tracked commodities this
     bloc's members are major producers/exporters of. THIS ONE IS AN
     EDITORIAL JUDGMENT CALL, not sourced from an API — flagged as
     such wherever it's surfaced (both here and in the frontend), with
     the reasoning shown per commodity so it can be reviewed/adjusted
     rather than presented as fact.
  4. EDGAR exposure  — reuses edgar_exposure.search_exposure() with
     the bloc's own name as the search term.

Bloc membership current as of Sept 2026 (BRICS expansion in
particular has moved fast and is worth re-checking periodically —
Saudi Arabia was invited in 2023 but reporting as of early-to-mid 2026
is inconsistent on whether it has formally joined; left OUT of the
default member list below pending a clearer, more consistent source,
noted in the comment on that bloc).
"""

import asyncio
import logging
from typing import Any

from . import macro as macro_mod
from . import edgar_exposure
from . import commodity_prices
from .store import intel_store

logger = logging.getLogger(__name__)

BLOCS = {
    "g7": {
        "name": "G7",
        "description": "Advanced-economy policy coordination bloc — rate decisions, sanctions coordination, currency policy.",
        "members": [
            ("USA", "United States"), ("GBR", "United Kingdom"), ("FRA", "France"),
            ("DEU", "Germany"), ("ITA", "Italy"), ("JPN", "Japan"), ("CAN", "Canada"),
        ],
    },
    "g20": {
        "name": "G20",
        "description": "G7 plus 12 more — the broadest bloc (~85% of world GDP), best read as a comparison table rather than one signal given how heterogeneous the membership is.",
        "members": [
            ("USA", "United States"), ("GBR", "United Kingdom"), ("FRA", "France"),
            ("DEU", "Germany"), ("ITA", "Italy"), ("JPN", "Japan"), ("CAN", "Canada"),
            ("ARG", "Argentina"), ("AUS", "Australia"), ("BRA", "Brazil"), ("CHN", "China"),
            ("IND", "India"), ("IDN", "Indonesia"), ("MEX", "Mexico"), ("RUS", "Russia"),
            ("SAU", "Saudi Arabia"), ("ZAF", "South Africa"), ("KOR", "South Korea"), ("TUR", "Turkey"),
            # The EU itself is also a G20 member alongside its constituent countries
            # (France/Germany/Italy above) — deliberately not double-counted here.
        ],
    },
    "brics": {
        "name": "BRICS",
        "description": "De-dollarization / alternative trade & payments bloc — different narrative from G7, not just 'emerging market G7'.",
        "members": [
            ("BRA", "Brazil"), ("RUS", "Russia"), ("IND", "India"), ("CHN", "China"),
            ("ZAF", "South Africa"), ("EGY", "Egypt"), ("ETH", "Ethiopia"), ("IRN", "Iran"),
            ("ARE", "United Arab Emirates"), ("IDN", "Indonesia"),
            # Saudi Arabia: invited 2023, reporting on formal accession is
            # inconsistent as of 2026 (some sources say joined July 2025,
            # others report it's still deliberately holding off) — left out
            # until that's unambiguous. Add ("SAU", "Saudi Arabia") once confirmed.
        ],
    },
    "asean": {
        "name": "ASEAN",
        "description": "Manufacturing / supply-chain bloc — the most directly commodity-relevant of the four blocs here.",
        "members": [
            ("IDN", "Indonesia"), ("MYS", "Malaysia"), ("PHL", "Philippines"), ("SGP", "Singapore"),
            ("THA", "Thailand"), ("VNM", "Vietnam"), ("BRN", "Brunei"), ("KHM", "Cambodia"),
            ("LAO", "Laos"), ("MMR", "Myanmar"),
        ],
    },
}

# Tier-2, editorial: which tracked commodities each bloc's members are
# major producers/exporters of, with the reasoning shown alongside so
# it can be sanity-checked rather than taken as sourced fact.
BLOC_COMMODITY_RELEVANCE = {
    "g7": [
        ("CL=F", "US and Canada are both top-5 global oil producers"),
        ("NG=F", "US and Canada are major natural gas exporters"),
        ("GC=F", "Gold has a direct monetary-policy/reserve-currency linkage to G7 central banks"),
        ("ZC=F", "The US is the world's top corn exporter"),
        ("ZW=F", "The US and Canada are both top-5 wheat exporters"),
    ],
    "g20": [
        ("CL=F", "Saudi Arabia, Russia, and the US are the world's top-3 oil producers"),
        ("BZ=F", "Saudi Arabia and Russia are top global oil exporters"),
        ("NG=F", "Russia, the US, and Saudi Arabia are major gas producers"),
        ("HG=F", "China is the world's top copper consumer/refiner; Australia a top producer"),
        ("GC=F", "China, Russia, and Australia are top-3 gold producers"),
        ("ZW=F", "Russia, the US, Argentina, India, and Australia are all top wheat exporters"),
        ("ZC=F", "The US, Argentina, and Brazil are the top-3 corn exporters"),
        ("CT=F", "India, China, Brazil, the US, and Turkey are all top-5 cotton producers"),
        ("SB=F", "Brazil and India are the top-2 global sugar producers"),
        ("KC=F", "Brazil and Indonesia are top-3 global coffee producers"),
    ],
    "brics": [
        ("CL=F", "Russia, the UAE, and Iran are major oil exporters"),
        ("BZ=F", "Russia, the UAE, and Iran are major oil exporters"),
        ("NG=F", "Russia and Iran hold the two largest gas reserves in the world"),
        ("HG=F", "Russia is a top-10 copper producer; China the top global refiner/consumer"),
        ("GC=F", "Russia, China, and South Africa are all top-6 gold producers"),
        ("ZW=F", "Russia is the world's single largest wheat exporter"),
        ("ZC=F", "Brazil and China are a top exporter and the top importer, respectively"),
        ("CT=F", "India and China are the top-2 global cotton producers"),
        ("SB=F", "Brazil and India are the top-2 global sugar producers"),
        ("KC=F", "Brazil is the world's largest coffee exporter by a wide margin"),
    ],
    "asean": [
        ("NG=F", "Indonesia and Malaysia are major LNG exporters"),
        ("HG=F", "Indonesia's Grasberg mine is one of the world's largest copper sources"),
        ("GC=F", "Indonesia is a top-10 global gold producer"),
        ("SB=F", "Thailand is one of the world's top sugar exporters"),
        ("KC=F", "Vietnam is the world's #2 coffee producer (mostly robusta)"),
    ],
}


def get_bloc_ids() -> list[str]:
    return list(BLOCS.keys())


def get_bloc_info(bloc_id: str) -> dict[str, Any]:
    bloc = BLOCS.get(bloc_id)
    if not bloc:
        return {}
    return {"id": bloc_id, "name": bloc["name"], "description": bloc["description"], "members": bloc["members"]}


async def get_bloc_macro(bloc_id: str) -> dict[str, Any]:
    bloc = BLOCS.get(bloc_id)
    if not bloc:
        return {"error": f"Unknown bloc. Valid: {list(BLOCS)}"}

    async def _one(iso3: str, name: str) -> dict[str, Any]:
        gdp, inflation = await asyncio.gather(
            macro_mod._world_bank_latest("NY.GDP.MKTP.KD.ZG", iso3),
            macro_mod._world_bank_latest("FP.CPI.TOTL.ZG", iso3),
        )
        return {
            "iso3": iso3, "country": name,
            "gdp_growth": gdp["value"] if gdp else None,
            "gdp_growth_date": gdp["date"] if gdp else None,
            "inflation": inflation["value"] if inflation else None,
            "inflation_date": inflation["date"] if inflation else None,
        }

    rows = await asyncio.gather(*[_one(iso3, name) for iso3, name in bloc["members"]])
    return {"bloc": bloc_id, "name": bloc["name"], "countries": list(rows)}


def get_bloc_commodities(bloc_id: str) -> dict[str, Any]:
    bloc = BLOCS.get(bloc_id)
    if not bloc:
        return {"error": f"Unknown bloc. Valid: {list(BLOCS)}"}
    relevance = BLOC_COMMODITY_RELEVANCE.get(bloc_id, [])
    all_commodities = {c["symbol"]: c for c in commodity_prices.get_commodities().get("commodities", [])}
    rows = []
    for symbol, reason in relevance:
        entry = {"symbol": symbol, "reason": reason}
        if symbol in all_commodities:
            entry.update(all_commodities[symbol])
        rows.append(entry)
    return {
        "bloc": bloc_id, "name": bloc["name"], "commodities": rows,
        "note": "Relevance is an editorial judgment call (major producer/exporter relationships), not sourced from an API — reasoning shown per commodity.",
    }


def get_bloc_tone(bloc_id: str, limit: int = 1000) -> dict[str, Any]:
    bloc = BLOCS.get(bloc_id)
    if not bloc:
        return {"error": f"Unknown bloc. Valid: {list(BLOCS)}"}

    search_terms = [bloc["name"].lower()] + [name.lower() for _iso3, name in bloc["members"]]
    events = intel_store.query(sources=["GDELT"], limit=limit)

    matched = []
    for e in events:
        title = (e.get("title") or "").lower()
        theme = (e.get("meta", {}).get("theme") or "").lower()
        text = f"{title} {theme}"
        if any(term in text for term in search_terms):
            matched.append(e)

    if not matched:
        return {
            "bloc": bloc_id, "name": bloc["name"], "event_count": 0, "avg_tone": None,
            "label": None, "sample_headlines": [],
        }

    tones = [e["meta"]["tone"] for e in matched if "tone" in e.get("meta", {})]
    avg_tone = round(sum(tones) / len(tones), 2) if tones else None
    label = None
    if avg_tone is not None:
        label = "deteriorating" if avg_tone < -5 else "tense" if avg_tone < -2 else "improving" if avg_tone > 5 else "neutral"

    matched.sort(key=lambda e: e.get("ts", ""), reverse=True)
    return {
        "bloc": bloc_id, "name": bloc["name"], "event_count": len(matched),
        "avg_tone": avg_tone, "label": label,
        "sample_headlines": [e.get("title", "") for e in matched[:6] if e.get("title")],
    }


async def get_bloc_exposure(bloc_id: str, days_back: int = 14, limit: int = 8) -> dict[str, Any]:
    bloc = BLOCS.get(bloc_id)
    if not bloc:
        return {"error": f"Unknown bloc. Valid: {list(BLOCS)}"}
    filings = await edgar_exposure.search_exposure(bloc["name"], days_back=days_back, limit=limit)
    return {"bloc": bloc_id, "name": bloc["name"], "days_back": days_back, "filings": filings, "count": len(filings)}
