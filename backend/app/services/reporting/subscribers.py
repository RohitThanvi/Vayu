"""
subscribers.py — persistence for the daily executive summary email list.

Same pattern as agri/db.py (SQLite, stdlib, no new dependency, small
enough volume that this is plenty). Single-opt-in by design, per the
explicit product ask ("Subscribe button where users only provide their
email") — no confirmation email round-trip. To keep this honest and
compliant despite skipping double opt-in, every report email carries a
one-click unsubscribe link keyed to a random per-subscriber token (not
the email itself, so the unsubscribe link can't be used to enumerate or
guess other subscribers' addresses).
"""

import logging
import re
import sqlite3
import threading
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent.parent / "subscribers.sqlite3"
_lock = threading.Lock()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock, _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                unsubscribe_token TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                unsubscribed_at TEXT
            );
            """
        )
        conn.commit()
    logger.info(f"subscribers db initialized at {DB_PATH}")


def is_valid_email(email: str) -> bool:
    return bool(email) and len(email) <= 254 and bool(_EMAIL_RE.match(email.strip()))


def add_subscriber(email: str) -> dict:
    """Idempotent: re-subscribing an existing (even previously
    unsubscribed) email reactivates it under its EXISTING token rather
    than minting a new row — keeps one stable unsubscribe link per
    email across subscribe/unsubscribe cycles."""
    email = email.strip().lower()
    with _lock, _connect() as conn:
        existing = conn.execute("SELECT * FROM subscribers WHERE email = ?", (email,)).fetchone()
        if existing:
            conn.execute("UPDATE subscribers SET active = 1, unsubscribed_at = NULL WHERE email = ?", (email,))
            conn.commit()
            return {"email": email, "already_subscribed": bool(existing["active"])}

        sub_id = str(uuid.uuid4())
        token = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO subscribers (id, email, unsubscribe_token, active, created_at) VALUES (?, ?, ?, 1, ?)",
            (sub_id, email, token, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return {"email": email, "already_subscribed": False}


def unsubscribe(token: str) -> bool:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT id FROM subscribers WHERE unsubscribe_token = ? AND active = 1", (token,)).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE subscribers SET active = 0, unsubscribed_at = ? WHERE unsubscribe_token = ?",
            (datetime.now(timezone.utc).isoformat(), token),
        )
        conn.commit()
        return True


def get_active_subscribers() -> List[dict]:
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT email, unsubscribe_token FROM subscribers WHERE active = 1").fetchall()
        return [dict(r) for r in rows]


init_db()
