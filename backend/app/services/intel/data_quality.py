"""Shared "how much can I trust this number?" descriptors for the Business tab.

A terminal-grade product states, next to every figure, where it came from, how old it is and whether it is stale. These
helpers are the one place that logic lives so every endpoint reports it the same way.
"""
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

YAHOO_SOURCE = "Yahoo Finance (unofficial chart API)"
YAHOO_DELAY_NOTE = "Typically 15–20 minutes delayed; no service-level guarantee. Reference prices — not for trading decisions."


def describe(cached_at: Optional[float], ttl_seconds: int, last_error: Optional[str], source: str = YAHOO_SOURCE,
             delay_note: str = YAHOO_DELAY_NOTE, now: Optional[float] = None) -> Dict[str, Any]:
    """Data-quality block: source, delay note, when it was fetched, its age, and whether it is stale.

    `stale` means the cache is more than two refresh intervals old (a refresh cycle was missed or failed), or that
    nothing has ever been fetched.
    """
    now = time.time() if now is None else now
    if not cached_at:
        return {"source": source, "delay_note": delay_note, "cached_at": None, "age_seconds": None,
                "stale": True, "ttl_seconds": ttl_seconds, "last_error": last_error}
    age = max(0, int(now - cached_at))
    return {"source": source, "delay_note": delay_note, "cached_at": cached_at, "age_seconds": age,
            "stale": age > 2 * ttl_seconds, "ttl_seconds": ttl_seconds, "last_error": last_error}


def merge_quotes(previous: Iterable[Dict[str, Any]], fresh: Iterable[Dict[str, Any]],
                 expected: Iterable[Tuple[str, ...]]) -> List[Dict[str, Any]]:
    """Fresh quotes win. A symbol that failed THIS cycle keeps its last good quote, flagged `stale`, instead of silently
    disappearing from the screen (a vanished row looks like "no data", a stale-flagged one tells the truth).
    Output follows the order of `expected` (rows whose first element is the symbol)."""
    prev_by = {q["symbol"]: q for q in previous}
    fresh_by = {q["symbol"]: q for q in fresh}
    out: List[Dict[str, Any]] = []
    for row in expected:
        sym = row[0]
        if sym in fresh_by:
            out.append({**fresh_by[sym], "stale": False})
        elif sym in prev_by:
            out.append({**prev_by[sym], "stale": True})
    return out
