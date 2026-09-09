"""
auth/db.py — user accounts + session tokens for the landing-page login
gate. SQLite, matching this project's established pattern (agri/db.py,
reporting/subscribers.py, intel/timeseries_store.py) — consistent, and
genuinely sufficient for this write volume (signups/logins, not a
high-frequency data feed).

Deliberately no new dependency for either password hashing or sessions:
- Passwords: PBKDF2-HMAC-SHA256 via hashlib (stdlib), 200k iterations —
  this is what Django's own default password hasher uses under the
  hood, not a home-rolled scheme; bcrypt/argon2 are marginally
  stronger but pulling in a compiled dependency for that margin isn't
  worth it at this project's scale.
- Sessions: opaque random tokens (secrets.token_urlsafe) stored in a
  sessions table with an expiry, checked against the DB per request —
  not JWT. This avoids needing a signing secret to manage/rotate at
  all, at the cost of one indexed DB lookup per authenticated request,
  which is a non-issue at this project's traffic.
"""

import hashlib
import hmac
import logging
import re
import secrets
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent.parent / "auth.sqlite3"
_lock = threading.Lock()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SESSION_TTL_DAYS = 30
PBKDF2_ITERATIONS = 200_000


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock, _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS password_resets (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
            """
        )
        conn.commit()
    logger.info(f"auth db initialized at {DB_PATH}")


def is_valid_email(email: str) -> bool:
    return bool(email) and len(email) <= 254 and bool(_EMAIL_RE.match(email.strip()))


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, digest_hex = stored_hash.split("$")
        salt = bytes.fromhex(salt_hex)
        recomputed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
        return hmac.compare_digest(recomputed.hex(), digest_hex)
    except Exception:
        return False


def create_user(email: str, password: str) -> Optional[dict]:
    """Returns the new user dict, or None if the email is already taken."""
    email = email.strip().lower()
    user_id = secrets.token_hex(16)
    password_hash = hash_password(password)
    with _lock, _connect() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            return None
        conn.execute(
            "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (user_id, email, password_hash, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    return {"id": user_id, "email": email}


def authenticate(email: str, password: str) -> Optional[dict]:
    email = email.strip().lower()
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return None
    return {"id": row["id"], "email": row["email"]}


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=SESSION_TTL_DAYS)
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, user_id, now.isoformat(), expires.isoformat()),
        )
        conn.commit()
    return token


def get_user_for_token(token: str) -> Optional[dict]:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT u.id, u.email, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?",
            (token,),
        ).fetchone()
    if not row:
        return None
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        with _lock, _connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()
        return None
    return {"id": row["id"], "email": row["email"]}


def delete_session(token: str):
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()


PASSWORD_RESET_TTL_MINUTES = 30


def get_user_by_email(email: str) -> Optional[dict]:
    email = email.strip().lower()
    with _lock, _connect() as conn:
        row = conn.execute("SELECT id, email FROM users WHERE email = ?", (email,)).fetchone()
    return dict(row) if row else None


def create_password_reset(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=PASSWORD_RESET_TTL_MINUTES)
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO password_resets (token, user_id, created_at, expires_at, used) VALUES (?, ?, ?, ?, 0)",
            (token, user_id, now.isoformat(), expires.isoformat()),
        )
        conn.commit()
    return token


def consume_password_reset(token: str, new_password: str) -> bool:
    """Validates the token (unused, unexpired), sets the new password,
    marks the token used, and invalidates every existing session for
    that user — a password reset is a strong signal something may have
    been compromised, so any session started before the reset (e.g. on
    a device that isn't the one requesting the reset) shouldn't survive
    it."""
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT user_id, expires_at, used FROM password_resets WHERE token = ?", (token,)
        ).fetchone()
        if not row or row["used"] or datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
            return False

        new_hash = hash_password(new_password)
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, row["user_id"]))
        conn.execute("UPDATE password_resets SET used = 1 WHERE token = ?", (token,))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["user_id"],))
        conn.commit()
    return True


init_db()
