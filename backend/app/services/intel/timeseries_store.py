"""
timeseries_store.py — lightweight SQLite persistence for periodic
snapshots, starting with maritime chokepoint vessel counts.

Same rationale as agri/db.py: vessel_store itself is intentionally
in-memory (it only tracks CURRENT vessel positions, overwritten per
MMSI — see its own docstring), which is correct for live map rendering
but means there's no history to compare "right now" against. Detecting
a genuine trade-route disruption ("vessel count at Hormuz is unusually
low") requires knowing what "usual" looks like, which requires a
history — hence this separate, deliberately durable store. SQLite
because the volume here is tiny (7 chokepoints × one row every 15
minutes) and it's already the established pattern in this codebase
(sqlite3 is stdlib, no new dependency).

Commodity price HISTORY is deliberately NOT duplicated here — Yahoo
Finance's response (see commodity_prices.py) already includes a
day-over-day change_pct straight from the source, so persisting our own
second copy of that would be redundant. Only the vessel-count side
needed a new store, since nothing upstream tracks that over time.
"""

import logging
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta
from statistics import mean, pstdev
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent.parent / "intel_timeseries.sqlite3"

_lock = threading.Lock()

# Below this many historical samples, a baseline is too thin to trust —
# the endpoint reports "insufficient_history" rather than a misleadingly
# precise-looking deviation percentage computed from 1-2 data points.
MIN_SAMPLES_FOR_BASELINE = 8

# How far back a baseline looks. 14 days at a 15-minute sampling
# interval is enough to smooth out normal daily/weekly traffic rhythm
# (weekday vs weekend transit patterns) without the store growing large
# or the baseline going stale relative to genuine longer-term shifts.
BASELINE_LOOKBACK_DAYS = 14


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock, _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chokepoint_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chokepoint_key TEXT NOT NULL,
                ts TEXT NOT NULL,
                vessel_count INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chokepoint_ts ON chokepoint_snapshots(chokepoint_key, ts);
            """
        )
    logger.info(f"timeseries_store initialized at {DB_PATH}")


def record_chokepoint_snapshot(chokepoint_key: str, vessel_count: int):
    ts = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO chokepoint_snapshots (chokepoint_key, ts, vessel_count) VALUES (?, ?, ?)",
            (chokepoint_key, ts, vessel_count),
        )
        # Keep the table small — drop anything older than 2x the lookback
        # window, since nothing beyond that is ever read.
        cutoff = (datetime.now(timezone.utc) - timedelta(days=BASELINE_LOOKBACK_DAYS * 2)).isoformat()
        conn.execute("DELETE FROM chokepoint_snapshots WHERE chokepoint_key = ? AND ts < ?", (chokepoint_key, cutoff))


def get_chokepoint_history(chokepoint_key: str, days: int = 14) -> list:
    """Raw (ts, vessel_count) samples for charting — the same rows
    get_chokepoint_baseline() already reads to compute mean/stddev,
    just returned as a series instead of collapsed into statistics."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT ts, vessel_count FROM chokepoint_snapshots WHERE chokepoint_key = ? AND ts >= ? ORDER BY ts ASC",
            (chokepoint_key, cutoff),
        ).fetchall()
    return [{"date": r["ts"], "value": r["vessel_count"]} for r in rows]


def get_chokepoint_baseline(chokepoint_key: str, current_count: int) -> Dict[str, Any]:
    """
    Compares `current_count` (the live vessel_store count, passed in
    rather than re-derived here, since vessel_store is the live source
    of truth and this module only knows about historical snapshots)
    against the rolling mean/stddev of the last BASELINE_LOOKBACK_DAYS
    of samples.

    Returns a deviation percentage and a status band — 'normal' /
    'elevated' / 'disrupted' — rather than a bare number, since a raw
    percentage alone doesn't tell you whether a swing is routine
    day-to-day noise or a genuine anomaly. The band requires BOTH a
    meaningful z-score AND a meaningful raw percentage move to agree
    before escalating — a chokepoint with naturally very low variance
    (e.g. a quiet route) would otherwise get "disrupted" flagged from a
    statistically-large-but-practically-tiny swing (a synthetic test
    with near-zero variance surfaced exactly this before the percentage
    floor was added).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=BASELINE_LOOKBACK_DAYS)).isoformat()
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT vessel_count FROM chokepoint_snapshots WHERE chokepoint_key = ? AND ts >= ? ORDER BY ts",
            (chokepoint_key, cutoff),
        ).fetchall()

    counts = [r["vessel_count"] for r in rows]
    if len(counts) < MIN_SAMPLES_FOR_BASELINE:
        return {
            "current_count": current_count,
            "baseline_mean": None,
            "deviation_pct": None,
            "z_score": None,
            "status": "insufficient_history",
            "sample_count": len(counts),
        }

    baseline_mean = mean(counts)
    baseline_stddev = pstdev(counts) if len(counts) > 1 else 0.0
    deviation_pct = ((current_count - baseline_mean) / baseline_mean * 100) if baseline_mean > 0 else 0.0
    z_score = ((current_count - baseline_mean) / baseline_stddev) if baseline_stddev > 0.5 else 0.0

    if abs(z_score) >= 2.0 and abs(deviation_pct) >= 15:
        status = "disrupted"
    elif abs(z_score) >= 1.0 and abs(deviation_pct) >= 8:
        status = "elevated"
    else:
        status = "normal"

    return {
        "current_count": current_count,
        "baseline_mean": round(baseline_mean, 1),
        "deviation_pct": round(deviation_pct, 1),
        "z_score": round(z_score, 2),
        "status": status,
        "sample_count": len(counts),
    }


init_db()
