import time
import threading
import logging
from typing import Any, Dict, Optional
from uuid import UUID

from ..core.config import settings

logger = logging.getLogger(__name__)


class JobStore:
    """Thread-safe in-memory job store with TTL-based cleanup."""

    def __init__(self, ttl_seconds: int = 3600):
        self._store: Dict[str, Dict[str, Any]] = {}
        self._timestamps: Dict[str, float] = {}
        self._lock = threading.RLock()
        self._ttl = ttl_seconds
        self._start_cleanup_thread()

    def set(self, job_id: UUID, data: Dict[str, Any]) -> None:
        key = str(job_id)
        with self._lock:
            self._store[key] = data
            self._timestamps[key] = time.monotonic()

    def update(self, job_id: UUID, data: Dict[str, Any]) -> None:
        key = str(job_id)
        with self._lock:
            if key in self._store:
                self._store[key].update(data)
                self._timestamps[key] = time.monotonic()

    def get(self, job_id: UUID) -> Optional[Dict[str, Any]]:
        key = str(job_id)
        with self._lock:
            return self._store.get(key)

    def delete(self, job_id: UUID) -> None:
        key = str(job_id)
        with self._lock:
            self._store.pop(key, None)
            self._timestamps.pop(key, None)

    def _cleanup(self) -> None:
        now = time.monotonic()
        with self._lock:
            expired = [
                k for k, ts in self._timestamps.items()
                if now - ts > self._ttl
            ]
            for k in expired:
                del self._store[k]
                del self._timestamps[k]
            if expired:
                logger.info(f"JobStore: cleaned up {len(expired)} expired job(s).")

    def _start_cleanup_thread(self) -> None:
        def run():
            while True:
                time.sleep(300)  # Run every 5 minutes
                try:
                    self._cleanup()
                except Exception as e:
                    logger.error(f"JobStore cleanup error: {e}")

        t = threading.Thread(target=run, daemon=True)
        t.start()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._store)


# Singleton instance
job_store = JobStore(ttl_seconds=settings.JOB_TTL_SECONDS)
