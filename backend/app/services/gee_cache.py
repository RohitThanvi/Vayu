"""
gee_cache.py — caching layer for expensive GEE-backed remote sensing
computations (see gee_remote_sensing.py, called from
api/remote_sensing_endpoints.py), so an identical repeat request (same
tool, same AOI, same dates/params) doesn't re-hit Earth Engine.

ACCURACY GUARANTEE: this is deliberately an EXACT-MATCH cache only — no
AOI-overlap matching, no "close enough" date ranges, no fuzzy anything.
The cache key is built from every parameter that can change the GEE
result (tool name + AOI geometry + dates + indices + training/reference
points + everything else in the request). A historical Earth Engine
query over a fixed geometry and fixed date range is deterministic — the
same satellite imagery, the same math, every time — so a cache hit and a
fresh compute are the SAME value by construction, never an approximation
of it. Caching here cannot make a result less accurate; at worst (a
provider outage) it makes a repeat request unavailable, same as any
cache.

PERFORMANCE: the cache lookup/store itself is a single fast key-value
op (Redis GET/SETEX, or a dict lookup for the in-memory fallback) — a
few ms at most, negligible next to the GEE computation it's guarding
(seconds, sometimes tens of seconds for the heavier tools). A cache MISS
still runs the exact same compute path as before this existed; nothing
about the actual GEE call changes.

BACKEND: real Redis if REDIS_URL is set (works with any standard Redis
provider, including Upstash's free tier over rediss://). Falls back to
an in-memory TTL dict (same pattern global_layers.py already uses for
its own, separate cache) when REDIS_URL is unset OR when Redis is
unreachable — Render Oregon has, in this project's history, blocked
specific outbound connections (see ais-bridge's AISStream/OpenSky/
CelesTrak notes), so a Redis connection failure is treated as
"degrade to memory", not "take the Remote Sensing tab down". The
in-memory fallback is per-process — it won't survive a Render restart
or be shared across multiple instances, which real Redis would fix once
reachable.
"""

import hashlib
import json
import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

from ..core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days — safe because results are
                                          # deterministic (see module docstring),
                                          # not a freshness tradeoff like a
                                          # "live conditions" cache would need


class _MemoryCache:
    """Thread-safe in-memory TTL cache. Same shape as global_layers.py's
    _cache/_cached, kept separate since that one is for whole-map layers
    with its own 12h freshness TTL — a different concern from this
    deterministic-result cache."""

    def __init__(self):
        self._store: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if time.time() - entry["cached_at"] > entry["ttl"]:
                del self._store[key]
                return None
            return entry["value"]

    def set(self, key: str, value: Any, ttl: int) -> None:
        with self._lock:
            self._store[key] = {"value": value, "cached_at": time.time(), "ttl": ttl}

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._store)


_memory_cache = _MemoryCache()
_redis_client = None
_redis_checked = False
_redis_lock = threading.Lock()


def _get_redis():
    """Lazily connect on first use (not at import time, so a missing/bad
    REDIS_URL never breaks app startup), then reuse the same client.
    Any failure — import, connect, or a later runtime error — permanently
    falls back to the in-memory cache for the rest of this process's
    life rather than retrying Redis on every request."""
    global _redis_client, _redis_checked
    if _redis_checked:
        return _redis_client
    with _redis_lock:
        if _redis_checked:
            return _redis_client
        _redis_checked = True
        if not settings.REDIS_URL:
            logger.info("gee_cache: REDIS_URL not set, using in-memory cache")
            return None
        try:
            import redis  # local import: optional dependency, only needed here
            client = redis.from_url(
                settings.REDIS_URL,
                socket_connect_timeout=5,
                socket_timeout=5,
                decode_responses=True,
            )
            client.ping()
            _redis_client = client
            logger.info("gee_cache: connected to Redis")
        except Exception as e:
            logger.warning(
                f"gee_cache: Redis unavailable ({type(e).__name__}: {e}), "
                f"falling back to in-memory cache for this process"
            )
            _redis_client = None
    return _redis_client


def _cache_key(tool: str, params: Dict[str, Any]) -> str:
    """Deterministic key from every param that can affect the result.
    sort_keys=True so key order never matters; default=str so any
    non-JSON-native value (shouldn't normally occur here) still hashes
    rather than raising."""
    blob = json.dumps({"tool": tool, "params": params}, sort_keys=True, default=str)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return f"vayu:rs:{tool}:{digest}"


def cached_compute(
    tool: str,
    params: Dict[str, Any],
    compute_fn: Callable[[], Any],
    ttl: int = DEFAULT_TTL_SECONDS,
) -> Any:
    """Look up (tool, params) in the cache; on miss, call compute_fn()
    (the real GEE computation, unchanged) and store the result. Callers
    never see a difference between a hit and a miss beyond latency —
    same return shape either way."""
    key = _cache_key(tool, params)

    client = _get_redis()
    if client is not None:
        try:
            hit = client.get(key)
            if hit is not None:
                logger.info(f"gee_cache: HIT (redis) {tool}")
                return json.loads(hit)
        except Exception as e:
            logger.warning(f"gee_cache: redis GET failed ({e}), computing fresh")
    else:
        hit = _memory_cache.get(key)
        if hit is not None:
            logger.info(f"gee_cache: HIT (memory) {tool}")
            return hit

    result = compute_fn()

    if client is not None:
        try:
            client.setex(key, ttl, json.dumps(result, default=str))
        except Exception as e:
            logger.warning(f"gee_cache: redis SETEX failed ({e}), result not cached")
    else:
        _memory_cache.set(key, result, ttl)

    return result
