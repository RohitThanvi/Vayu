"""
auth/db.py — user accounts + session tokens for the landing-page login
gate. Previously SQLite (backend/auth.sqlite3); migrated to Postgres
(Supabase) because Render's free web service has no persistent disk —
every restart wiped that file, which is why signup would work and a
later login would fail with "incorrect email or password" (a real,
freshly-empty table, not a hashing bug — the hashing/verification logic
here is unchanged from the SQLite version, byte for byte).

Connects via DATABASE_URL (see core/config.py). Deliberately fails soft:
if the DB is unreachable at startup, the pool is left as None and
is_available() returns False — the REST OF THE APP still boots and
serves satellite/maritime/agri/etc. normally; only the auth/contact-admin
endpoints return a 503 instead of crashing the whole process on import.
See auth_endpoints.py for how that 503 is surfaced.

Passwords: still PBKDF2-HMAC-SHA256 via hashlib (stdlib), 200k
iterations — same scheme as before, not something that needed changing.
Sessions: still opaque random tokens (secrets.token_urlsafe), now in a
Postgres table instead of a SQLite one — same design, no JWT.

New in this version: `name`, `service_tier` (free/agri/business/full)
and optional `organization`/`use_case` on signup, laying the groundwork
for the paid-tier plan — see tier_gate.py for the (currently unattached)
enforcement dependency other routers can opt into later.
"""

import hashlib
import hmac
import logging
import re
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SESSION_TTL_DAYS = 30
PBKDF2_ITERATIONS = 200_000
PASSWORD_RESET_TTL_MINUTES = 30

SERVICE_TIERS = ("free", "agri", "business", "full")
DEFAULT_TIER = "free"

_pool = None


class AuthDBUnavailable(Exception):
    """Raised when the Postgres pool couldn't be created (bad/missing
    DATABASE_URL, host unreachable, etc.) — endpoints catch this and
    return a 503 rather than letting it become an unhandled 500."""


def _init_pool():
    global _pool
    from ...core.config import settings
    if not settings.DATABASE_URL:
        logger.warning("auth db: DATABASE_URL not set — auth endpoints will return 503 until it is.")
        return
    try:
        import psycopg2.pool
        _pool = psycopg2.pool.ThreadedConnectionPool(1, 10, dsn=settings.DATABASE_URL)
        logger.info("auth db: connected (Postgres)")
    except Exception as e:
        logger.error(f"auth db: could not connect to Postgres — {type(e).__name__}: {e}")
        _pool = None


def is_available() -> bool:
    return _pool is not None


@contextmanager
def _conn():
    if _pool is None:
        raise AuthDBUnavailable("Auth database is not connected.")
    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def init_db():
    _init_pool()
    if not is_available():
        return
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                service_tier TEXT NOT NULL DEFAULT 'free',
                organization TEXT,
                use_case TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at TIMESTAMPTZ NOT NULL
            );
            CREATE TABLE IF NOT EXISTS password_resets (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at TIMESTAMPTZ NOT NULL,
                used BOOLEAN NOT NULL DEFAULT false
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
            """
        )
    logger.info("auth db: schema ready")


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


def create_user(name: str, email: str, password: str, service_tier: str = DEFAULT_TIER,
                 organization: Optional[str] = None, use_case: Optional[str] = None) -> Optional[dict]:
    """Returns the new user dict, or None if the email is already taken."""
    email = email.strip().lower()
    service_tier = service_tier if service_tier in SERVICE_TIERS else DEFAULT_TIER
    user_id = secrets.token_hex(16)
    password_hash = hash_password(password)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            return None
        cur.execute(
            "INSERT INTO users (id, name, email, password_hash, service_tier, organization, use_case, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (user_id, name.strip(), email, password_hash, service_tier,
             (organization or "").strip() or None, (use_case or "").strip() or None,
             datetime.now(timezone.utc)),
        )
    return {"id": user_id, "name": name.strip(), "email": email, "service_tier": service_tier}


def authenticate(email: str, password: str) -> Optional[dict]:
    email = email.strip().lower()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name, email, password_hash, service_tier FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
    if not row or not verify_password(password, row[3]):
        return None
    return {"id": row[0], "name": row[1], "email": row[2], "service_tier": row[4]}


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=SESSION_TTL_DAYS)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (%s, %s, %s, %s)",
            (token, user_id, now, expires),
        )
    return token


def get_user_for_token(token: str) -> Optional[dict]:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT u.id, u.name, u.email, u.service_tier, s.expires_at "
            "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = %s",
            (token,),
        )
        row = cur.fetchone()
        if not row:
            return None
        if row[4] < datetime.now(timezone.utc):
            cur.execute("DELETE FROM sessions WHERE token = %s", (token,))
            return None
    return {"id": row[0], "name": row[1], "email": row[2], "service_tier": row[3]}


def delete_session(token: str):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM sessions WHERE token = %s", (token,))


def get_user_by_email(email: str) -> Optional[dict]:
    email = email.strip().lower()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name, email, service_tier FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
    return {"id": row[0], "name": row[1], "email": row[2], "service_tier": row[3]} if row else None


def list_users(limit: int = 500) -> list[dict]:
    """Never the password hash, even for the admin view."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT name, email, service_tier, organization, use_case, created_at "
            "FROM users ORDER BY created_at DESC LIMIT %s",
            (limit,),
        )
        cols = ["name", "email", "service_tier", "organization", "use_case", "created_at"]
        rows = cur.fetchall()
    return [dict(zip(cols, r)) for r in rows]


def create_password_reset(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=PASSWORD_RESET_TTL_MINUTES)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO password_resets (token, user_id, created_at, expires_at, used) VALUES (%s, %s, %s, %s, false)",
            (token, user_id, now, expires),
        )
    return token


def consume_password_reset(token: str, new_password: str) -> bool:
    """Validates the token (unused, unexpired), sets the new password,
    marks the token used, and invalidates every existing session for
    that user."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT user_id, expires_at, used FROM password_resets WHERE token = %s", (token,))
        row = cur.fetchone()
        if not row or row[2] or row[1] < datetime.now(timezone.utc):
            return False
        user_id = row[0]
        new_hash = hash_password(new_password)
        cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (new_hash, user_id))
        cur.execute("UPDATE password_resets SET used = true WHERE token = %s", (token,))
        cur.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
    return True


init_db()
