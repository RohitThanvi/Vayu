"""
sanctions.py — cross-references live-tracked vessels (vessel_store.py)
against the US Treasury OFAC Specially Designated Nationals (SDN) list.

Data source: OFAC's consolidated SDN list, published as free CSV, no
API key — https://sanctionslistservice.ofac.treas.gov/api/download/csv
(Treasury's documented free bulk-download endpoint). Refreshed on a
long TTL below since Treasury updates this occasionally, not per
minute.

Matching approach and its real limitation: the SDN list identifies
vessels primarily by NAME and IMO number, not MMSI — and the AIS
stream this project tracks gives MMSI + vessel name, not IMO (IMO
numbers require a separate, non-free registry lookup to obtain per
MMSI). So this is a NAME match against the SDN vessel-type entries,
normalized (uppercased, punctuation stripped) — which means it can
miss vessels that have since been renamed (a known, common sanctions-
evasion tactic) and can in principle false-positive on an innocent
vessel that happens to share a name with a listed one. This is
disclosed in the API response itself (`match_confidence`), not just
here, since it directly affects how the result should be used.
"""

import csv
import io
import logging
import re
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

OFAC_SDN_CSV_URL = "https://sanctionslistservice.ofac.treas.gov/api/download/csv"
CACHE_TTL_SECONDS = 6 * 3600  # SDN list changes rarely; no need to refetch often

_sdn_vessel_names: set[str] = set()   # normalized names, vessel-type SDN entries only
_last_fetch: float = 0.0


def _normalize(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


async def _refresh_sdn_list():
    global _sdn_vessel_names, _last_fetch
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(OFAC_SDN_CSV_URL, timeout=30)
            resp.raise_for_status()
            text = resp.text
    except Exception as e:
        logger.warning(f"OFAC SDN list fetch failed: {type(e).__name__}: {e}")
        return  # keep whatever we had cached, even if stale — better than nothing

    names = set()
    try:
        # OFAC's SDN CSV has no header row; columns are positional. The
        # vessel name lives in column 2 (index 1) for entries where
        # column 3 (SDN_Type, index 2) == 'vessel'. See Treasury's SDN
        # data spec for the full column layout.
        reader = csv.reader(io.StringIO(text))
        for row in reader:
            if len(row) < 3:
                continue
            sdn_type = (row[2] or "").strip().lower()
            if sdn_type != "vessel":
                continue
            name = (row[1] or "").strip()
            if name:
                names.add(_normalize(name))
    except Exception as e:
        logger.error(f"OFAC SDN CSV parse failed: {type(e).__name__}: {e}")
        return

    if names:
        _sdn_vessel_names = names
        _last_fetch = time.time()
        logger.info(f"OFAC SDN list refreshed: {len(names)} vessel entries")


async def _ensure_fresh():
    if time.time() - _last_fetch > CACHE_TTL_SECONDS:
        await _refresh_sdn_list()


async def screen_vessels(vessels: list[dict]) -> list[dict]:
    """vessels: list of vessel dicts from vessel_store (must have
    'name' and 'mmsi'). Returns only the ones that matched, each
    annotated with match_confidence."""
    await _ensure_fresh()
    if not _sdn_vessel_names:
        return []  # list unavailable (never fetched successfully) — fail closed, not with false alarms

    hits = []
    for v in vessels:
        name = v.get("name", "")
        if not name or name.startswith("MMSI "):
            continue
        norm = _normalize(name)
        if norm and norm in _sdn_vessel_names:
            hits.append({
                "mmsi": v.get("mmsi"),
                "name": name,
                "lat": v.get("lat"),
                "lon": v.get("lon"),
                "category": v.get("category"),
                "match_confidence": "name-only — not confirmed by IMO number, see caveat",
            })
    return hits


def get_list_status() -> dict[str, Any]:
    return {
        "loaded": bool(_sdn_vessel_names),
        "vessel_entries": len(_sdn_vessel_names),
        "last_refreshed": _last_fetch or None,
    }
