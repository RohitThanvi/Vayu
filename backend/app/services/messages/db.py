"""
messages/db.py — durable storage for landing-page "Contact us"
submissions, so a message survives even when outbound email (Resend/
Gmail SMTP) is unconfigured or failing — see reporting/email_sender.py.
Previously the contact endpoint only ever emailed the message and threw
it away on any send failure; now it's written here FIRST, unconditionally,
and the email is just a best-effort notification on top.

SQLite, same established pattern as auth/db.py, agri/db.py, etc. Not
tracked in git like some of this project's other .sqlite3 files — this
one holds contact-form senders' emails, which don't belong in commit
history.
"""

import logging
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("MESSAGES_DB_PATH", str(Path(__file__).parent.parent.parent.parent / "messages.sqlite3")))
_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock, _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS contact_messages (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                emailed INTEGER NOT NULL DEFAULT 0,
                responded INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_contact_messages_created ON contact_messages(created_at);
            """
        )
        conn.commit()
    logger.info(f"messages db initialized at {DB_PATH}")


def save_message(name: str, email: str, message: str, emailed: bool) -> str:
    msg_id = secrets.token_hex(12)
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO contact_messages (id, name, email, message, created_at, emailed, responded) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (msg_id, name.strip(), email.strip(), message.strip(), datetime.now(timezone.utc).isoformat(), int(emailed)),
        )
        conn.commit()
    return msg_id


def list_messages(limit: int = 200, include_responded: bool = True) -> list[dict]:
    query = "SELECT * FROM contact_messages"
    if not include_responded:
        query += " WHERE responded = 0"
    query += " ORDER BY created_at DESC LIMIT ?"
    with _lock, _connect() as conn:
        rows = conn.execute(query, (limit,)).fetchall()
    return [dict(r) for r in rows]


def mark_responded(message_id: str, responded: bool = True) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "UPDATE contact_messages SET responded = ? WHERE id = ?",
            (int(responded), message_id),
        )
        conn.commit()
        return cur.rowcount > 0


def get_message(message_id: str) -> Optional[dict]:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM contact_messages WHERE id = ?", (message_id,)).fetchone()
    return dict(row) if row else None


init_db()
