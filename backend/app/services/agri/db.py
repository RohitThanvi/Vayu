"""
db.py — lightweight SQLite persistence for the agriculture module.

Deliberately NOT the in-memory pattern used by intel_store — watchlist regions,
fired alerts, and farmer/officer feedback need to survive a process restart
(Render free tier restarts on deploys/idles), and the volume here is small
enough that SQLite is plenty. No new dependency: sqlite3 is stdlib.

Generalized by design: a "region" is just a name + polygon + optional crop
label supplied by whoever creates it. Nothing here is hardcoded to a
particular district or crop — anyone can register any AOI.
"""

import json
import logging
import sqlite3
import threading
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent.parent / "agri_data.sqlite3"

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock, _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS regions (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                crop TEXT,
                aoi_geojson TEXT NOT NULL,
                risk_threshold REAL NOT NULL DEFAULT 60.0,
                owner_role TEXT NOT NULL DEFAULT 'farmer',
                phone TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY,
                region_id TEXT NOT NULL,
                risk_score REAL NOT NULL,
                confidence REAL NOT NULL,
                reason TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(region_id) REFERENCES regions(id)
            );

            CREATE TABLE IF NOT EXISTS feedback (
                id TEXT PRIMARY KEY,
                alert_id TEXT NOT NULL,
                accurate INTEGER NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(alert_id) REFERENCES alerts(id)
            );
            """
        )
        conn.commit()
    logger.info(f"agri db initialized at {DB_PATH}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Regions (watchlist) ─────────────────────────────────────────────────────

def create_region(name: str, aoi_geojson: dict, crop: Optional[str] = None,
                   risk_threshold: float = 60.0, owner_role: str = "farmer",
                   phone: Optional[str] = None) -> dict:
    region_id = str(uuid.uuid4())
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO regions (id, name, crop, aoi_geojson, risk_threshold, owner_role, phone, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (region_id, name, crop, json.dumps(aoi_geojson), risk_threshold, owner_role, phone, _now()),
        )
        conn.commit()
    return get_region(region_id)


def get_region(region_id: str) -> Optional[dict]:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM regions WHERE id = ?", (region_id,)).fetchone()
        return dict(row) if row else None


def list_regions(owner_role: Optional[str] = None) -> list[dict]:
    with _lock, _connect() as conn:
        if owner_role:
            rows = conn.execute("SELECT * FROM regions WHERE owner_role = ? ORDER BY created_at DESC", (owner_role,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM regions ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def delete_region(region_id: str) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM regions WHERE id = ?", (region_id,))
        conn.commit()
        return cur.rowcount > 0


# ── Alerts ───────────────────────────────────────────────────────────────────

def create_alert(region_id: str, risk_score: float, confidence: float,
                  reason: str, metrics: dict) -> dict:
    alert_id = str(uuid.uuid4())
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO alerts (id, region_id, risk_score, confidence, reason, metrics_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (alert_id, region_id, risk_score, confidence, reason, json.dumps(metrics), _now()),
        )
        conn.commit()
    return get_alert(alert_id)


def get_alert(alert_id: str) -> Optional[dict]:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        return dict(row) if row else None


def list_alerts(region_id: Optional[str] = None, limit: int = 100) -> list[dict]:
    with _lock, _connect() as conn:
        if region_id:
            rows = conn.execute(
                "SELECT * FROM alerts WHERE region_id = ? ORDER BY created_at DESC LIMIT ?",
                (region_id, limit),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


# ── Feedback ─────────────────────────────────────────────────────────────────

def create_feedback(alert_id: str, accurate: bool, comment: Optional[str] = None) -> dict:
    feedback_id = str(uuid.uuid4())
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO feedback (id, alert_id, accurate, comment, created_at) VALUES (?, ?, ?, ?, ?)",
            (feedback_id, alert_id, int(accurate), comment, _now()),
        )
        conn.commit()
    return {"id": feedback_id, "alert_id": alert_id, "accurate": accurate, "comment": comment}


def feedback_accuracy_rate(region_id: Optional[str] = None) -> dict:
    """
    Rolling accuracy of past alerts based on farmer/officer feedback.
    This is the number that should actually earn trust over time — surfaced
    back into future confidence scores by risk_scoring.confidence_for_region().
    """
    with _lock, _connect() as conn:
        if region_id:
            rows = conn.execute(
                """SELECT f.accurate FROM feedback f
                   JOIN alerts a ON f.alert_id = a.id
                   WHERE a.region_id = ?""",
                (region_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT accurate FROM feedback").fetchall()
        total = len(rows)
        if total == 0:
            return {"total_feedback": 0, "accuracy_rate": None}
        correct = sum(r["accurate"] for r in rows)
        return {"total_feedback": total, "accuracy_rate": round(correct / total, 4)}


init_db()
