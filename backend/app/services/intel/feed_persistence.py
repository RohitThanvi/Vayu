"""Optional persistence of the intel feed across restarts.

Why: the free-tier host spins the backend down when idle, and the in-memory store dies with it. The next visitor then
triggered a cold start whose backfill is only what the sources still publish (GDELT, for instance, exposes just its
latest 15-minute file), so that visitor saw a thinner feed than people who'd been connected for hours. Persisting the
newest events lets a restarted server come back with the same feed it had.

Uses the same optional Redis as the Spectra result cache (REDIS_URL). Without Redis (or if it fails) this is a no-op and
the feed is simply rebuilt from the sources, exactly as before. Nothing here may ever break startup or polling.
"""
import base64
import json
import logging
import zlib
from typing import List, Optional

logger = logging.getLogger(__name__)

KEY = "vayu:intel:feed:v1"
MAX_PERSISTED_EVENTS = 1000     # newest N; ~tens of KB after compression, well within free-tier request limits
TTL_SECONDS = 26 * 60 * 60      # a little over the 24 h event TTL, so a restart in the window always finds it


def _redis():
    from ..gee_cache import _get_redis   # shared lazy client; None when REDIS_URL is unset or Redis is unreachable
    return _get_redis()


def encode(events: List[dict]) -> str:
    return base64.b64encode(zlib.compress(json.dumps(events, separators=(",", ":")).encode(), 6)).decode()


def decode(blob: str) -> List[dict]:
    return json.loads(zlib.decompress(base64.b64decode(blob)).decode())


def save(events: List[dict]) -> bool:
    """Blocking (call from a thread). True if written."""
    try:
        client = _redis()
        if client is None:
            return False
        client.set(KEY, encode(events[:MAX_PERSISTED_EVENTS]), ex=TTL_SECONDS)
        return True
    except Exception as e:
        logger.warning(f"feed_persistence: save failed ({type(e).__name__}: {e})")
        return False


def load() -> Optional[List[dict]]:
    """Blocking (call from a thread). The persisted events, or None if unavailable."""
    try:
        client = _redis()
        if client is None:
            return None
        blob = client.get(KEY)
        if not blob:
            return None
        if isinstance(blob, bytes):
            blob = blob.decode()
        events = decode(blob)
        return events if isinstance(events, list) else None
    except Exception as e:
        logger.warning(f"feed_persistence: load failed ({type(e).__name__}: {e})")
        return None
