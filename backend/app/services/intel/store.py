"""
In-memory intelligence event store with TTL and deduplication.
Acts as the single source of truth for all live OSINT events.
No Redis needed for V1 — fits comfortably in memory for 10k events.
"""

import asyncio
import logging
import hashlib
from datetime import datetime, timedelta
from typing import Optional
from collections import deque

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
MAX_EVENTS = 2000          # max events held in memory
EVENT_TTL_HOURS = 24       # events older than this are purged
DEDUP_WINDOW_MINUTES = 30  # suppress duplicate events within this window


class IntelStore:
    """
    Thread-safe, async-compatible in-memory event store.

    Features:
    - Deduplication by spatial hash + source within a time window
    - TTL-based expiry
    - Per-source and per-severity filtering
    - AOI bounding box filtering for map queries
    - WebSocket subscriber registry
    """

    def __init__(self):
        self._events: deque = deque(maxlen=MAX_EVENTS)
        self._seen_hashes: dict[str, datetime] = {}  # hash -> first seen
        self._lock = asyncio.Lock()
        self._subscribers: set = set()   # WebSocket queues
        self._stats = {
            "total_ingested": 0,
            "total_deduplicated": 0,
            "total_expired": 0,
        }

    # ── Dedup ─────────────────────────────────────────────────────────────────
    def _dedup_hash(self, event: dict) -> str:
        """
        Hash based on source + rounded coordinates + tag.
        Rounds lat/lon to 1 decimal (~11km grid) to catch near-duplicate pins.
        """
        key = (
            f"{event['source']}"
            f"|{event['tag']}"
            f"|{round(event['lat'], 1)}"
            f"|{round(event['lon'], 1)}"
        )
        return hashlib.md5(key.encode()).hexdigest()[:12]

    def _is_duplicate(self, event: dict) -> bool:
        h = self._dedup_hash(event)
        if h not in self._seen_hashes:
            self._seen_hashes[h] = datetime.utcnow()
            return False
        age = datetime.utcnow() - self._seen_hashes[h]
        if age > timedelta(minutes=DEDUP_WINDOW_MINUTES):
            # Window expired — allow through, reset timer
            self._seen_hashes[h] = datetime.utcnow()
            return False
        return True

    # ── Ingestion ─────────────────────────────────────────────────────────────
    async def ingest(self, events: list[dict]) -> int:
        """Ingest a batch of events. Returns count of new events accepted."""
        async with self._lock:
            accepted = []
            for event in events:
                self._stats["total_ingested"] += 1
                if self._is_duplicate(event):
                    self._stats["total_deduplicated"] += 1
                    continue
                self._events.appendleft(event)
                accepted.append(event)

            # Purge expired seen hashes to prevent memory leak
            cutoff = datetime.utcnow() - timedelta(minutes=DEDUP_WINDOW_MINUTES * 2)
            self._seen_hashes = {
                k: v for k, v in self._seen_hashes.items() if v > cutoff
            }

        # Broadcast new events to all WebSocket subscribers
        if accepted:
            await self._broadcast(accepted)

        logger.info(f"Store: ingested {len(accepted)} new / {len(events)-len(accepted)} duped")
        return len(accepted)

    # ── Expiry ────────────────────────────────────────────────────────────────
    async def purge_expired(self):
        """Remove events older than EVENT_TTL_HOURS. Call periodically."""
        async with self._lock:
            cutoff = datetime.utcnow() - timedelta(hours=EVENT_TTL_HOURS)
            before = len(self._events)
            self._events = deque(
                (e for e in self._events
                 if datetime.fromisoformat(e["ts"].rstrip("Z")) > cutoff),
                maxlen=MAX_EVENTS,
            )
            expired = before - len(self._events)
            self._stats["total_expired"] += expired
            if expired:
                logger.info(f"Store: purged {expired} expired events")

    # ── Query ─────────────────────────────────────────────────────────────────
    def query(
        self,
        sources: Optional[list[str]] = None,
        severities: Optional[list[str]] = None,
        tags: Optional[list[str]] = None,
        bbox: Optional[tuple[float, float, float, float]] = None,  # (min_lat, min_lon, max_lat, max_lon)
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict]:
        """
        Filter events with optional source, severity, tag, and bounding box.
        Returns paginated results, newest first.
        """
        results = []
        for event in self._events:
            if sources and event["source"] not in sources:
                continue
            if severities and event["severity"] not in severities:
                continue
            if tags and not any(t.upper() in event["tag"].upper() for t in tags):
                continue
            if bbox:
                min_lat, min_lon, max_lat, max_lon = bbox
                if not (min_lat <= event["lat"] <= max_lat and
                        min_lon <= event["lon"] <= max_lon):
                    continue
            results.append(event)

        return results[offset: offset + limit]

    def get_all(self, limit: int = 200) -> list[dict]:
        return list(self._events)[:limit]

    def get_snapshot(self, per_source_limit: int = 15, total_limit: int = 60) -> list[dict]:
        """Initial-connection snapshot, guaranteeing every source that has
        events gets a fair share of the slots — get_all()'s plain 'last N
        overall' can starve a lower-volume source out entirely. Confirmed
        real case: GDELT only matches a narrow set of disaster/conflict
        themes (far fewer events per poll than USGS's global 5-min
        earthquake feed or FIRMS's frequently-numerous fire detections),
        so its events could sit further back in the deque and never appear
        in a plain top-50 slice, even though they're genuinely present and
        recent — a freshly-connected client would then see zero GDELT
        markers until the next live GDELT push, which could be several
        minutes away depending on where its poll cycle currently sits.
        Takes each source's most recent per_source_limit events, merges,
        and returns newest-first, capped at total_limit overall."""
        by_source: dict[str, list[dict]] = {}
        for event in self._events:  # already newest-first (appendleft)
            src = event.get("source", "unknown")
            bucket = by_source.setdefault(src, [])
            if len(bucket) < per_source_limit:
                bucket.append(event)

        merged = [e for bucket in by_source.values() for e in bucket]
        merged.sort(key=lambda e: e.get("ts", ""), reverse=True)
        return merged[:total_limit]

    def get_stats(self) -> dict:
        source_counts = {}
        severity_counts = {"info": 0, "warn": 0, "critical": 0}
        for e in self._events:
            source_counts[e["source"]] = source_counts.get(e["source"], 0) + 1
            severity_counts[e.get("severity", "info")] = \
                severity_counts.get(e.get("severity", "info"), 0) + 1
        return {
            **self._stats,
            "current_events": len(self._events),
            "by_source": source_counts,
            "by_severity": severity_counts,
        }

    # ── WebSocket pub/sub ─────────────────────────────────────────────────────
    def subscribe(self, queue: asyncio.Queue):
        self._subscribers.add(queue)
        logger.info(f"Store: +subscriber (total={len(self._subscribers)})")

    def unsubscribe(self, queue: asyncio.Queue):
        self._subscribers.discard(queue)
        logger.info(f"Store: -subscriber (total={len(self._subscribers)})")

    async def _broadcast(self, events: list[dict]):
        if not self._subscribers:
            return
        dead = set()
        for queue in self._subscribers:
            try:
                for event in events:
                    queue.put_nowait(event)
            except asyncio.QueueFull:
                dead.add(queue)
        for q in dead:
            self._subscribers.discard(q)


# ── Singleton ─────────────────────────────────────────────────────────────────
intel_store = IntelStore()
