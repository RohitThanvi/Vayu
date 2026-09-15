"""
auth/clerk_auth.py — verifies Clerk session tokens (RS256 JWTs) against
the PEM public key in settings.CLERK_JWT_KEY.

This is "networkless" verification: Clerk signs every session token
with a key pair, and instead of fetching their JWKS over the network on
every request (extra latency, and a hard dependency on Clerk's uptime
and on this backend having egress to clerk-domain endpoints), we
verify locally against the public half of that key, pasted into an env
var once. See config.py for where to get it.

Deliberately the ONLY place that touches Clerk verification — every
router either depends on get_current_user/require_tier in tier_gate.py,
or it doesn't require auth at all. No endpoint should decode a token
itself.
"""

import logging
from typing import Optional

import jwt
from jwt import PyJWTError

logger = logging.getLogger(__name__)

# Clerk mints session tokens with a short TTL (default ~60s) and expects
# the frontend to silently refresh them — leeway here is just clock-skew
# tolerance between this server and Clerk's, not a way to accept stale
# tokens.
_LEEWAY_SECONDS = 10


class ClerkAuthUnavailable(Exception):
    """CLERK_JWT_KEY isn't configured. Distinct from an invalid/expired
    token (that's just "not logged in") — this means the deployment
    itself is misconfigured, so callers surface a 503, not a 401."""


class ClerkTokenInvalid(Exception):
    """Token missing, malformed, expired, or signature didn't verify."""


def _public_key():
    from ...core.config import settings
    pem = settings.CLERK_JWT_KEY.strip()
    if not pem:
        raise ClerkAuthUnavailable("CLERK_JWT_KEY is not set.")
    # Clerk's dashboard gives the raw PEM; tolerate it being pasted as a
    # single-line env var with literal "\n" sequences (common on Render).
    if "\\n" in pem and "\n" not in pem:
        pem = pem.replace("\\n", "\n")
    return pem


def verify_session_token(token: str) -> dict:
    """Returns the decoded claims dict on success. Raises
    ClerkAuthUnavailable or ClerkTokenInvalid otherwise — callers map
    those to 503 / 401 respectively, never a bare 500."""
    if not token:
        raise ClerkTokenInvalid("No token provided.")
    key = _public_key()
    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            leeway=_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "sub"]},
        )
    except PyJWTError as exc:
        raise ClerkTokenInvalid(str(exc)) from exc
    return claims


def extract_user(claims: dict) -> dict:
    """Normalizes Clerk claims into the {id, email, name, service_tier}
    shape the rest of the app expects (same shape the old Postgres
    auth_db.get_user_for_token returned, so tier_gate.py's callers
    didn't need to change). service_tier is read from public_metadata,
    set via the Clerk Dashboard or a backend call to the Clerk Backend
    API — defaults to 'free' for any user who hasn't been assigned one,
    same default as the old system (auth_db.DEFAULT_TIER)."""
    public_metadata = claims.get("public_metadata") or claims.get("metadata") or {}
    return {
        "id": claims.get("sub"),
        "email": claims.get("email") or "",
        "name": claims.get("name") or claims.get("first_name") or "",
        "service_tier": public_metadata.get("service_tier", "free"),
    }
