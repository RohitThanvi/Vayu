"""
auth/tier_gate.py — FastAPI dependencies for (a) resolving the logged-in
user from their Clerk session token, and (b) requiring a minimum
service tier before an endpoint runs.

Now WIRED UP (see main.py) — every data router except the public
/contact form and /health is gated behind at least get_current_user.
This is deliberate, per explicit instruction: hiding a panel in the
frontend is a UI choice, not security — anyone can still call the API
directly with curl/Postman and get the data regardless of what's
rendered. The checks below run server-side, per request, against the
token's actual owner, so a missing or invalid token is rejected before
any handler code runs, not after.
"""

import logging

from fastapi import Header, HTTPException

from . import clerk_auth

logger = logging.getLogger(__name__)


async def get_current_user(authorization: str = Header(default="")) -> dict:
    """Resolves the bearer token to a user dict ({id, email, name,
    service_tier}), or raises 401/503. Use this alone (without a tier
    requirement) on any endpoint that just needs "must be logged in,
    tier doesn't matter"."""
    token = authorization.replace("Bearer ", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Not logged in.")
    try:
        claims = clerk_auth.verify_session_token(token)
    except clerk_auth.ClerkAuthUnavailable:
        # Misconfigured deployment (CLERK_JWT_KEY unset) — fail closed,
        # not open. Every route behind this dependency stays inaccessible
        # rather than silently letting requests through unauthenticated.
        logger.error("Clerk auth misconfigured: CLERK_JWT_KEY not set.")
        raise HTTPException(status_code=503, detail="Auth service is temporarily unavailable.")
    except clerk_auth.ClerkTokenInvalid:
        raise HTTPException(status_code=401, detail="Session expired or invalid.")
    return clerk_auth.extract_user(claims)


def require_tier(*allowed_tiers: str):
    """FastAPI dependency factory — use as
    `Depends(require_tier("business", "full"))` on a route (or in a
    router's `dependencies=[...]`) to require the caller be logged in
    AND on one of the listed tiers. 'full' should generally always be
    included in the allowed set, as it's meant to include everything.

    Raises 401 (not logged in / bad session) or 403 (logged in, but
    tier doesn't include this feature) — kept distinct so the frontend
    can tell "log in" from "upgrade" apart.
    """
    async def _dependency(authorization: str = Header(default="")):
        user = await get_current_user(authorization)
        if user["service_tier"] not in allowed_tiers:
            raise HTTPException(
                status_code=403,
                detail=f"This feature requires one of: {', '.join(allowed_tiers)}. Your plan: {user['service_tier']}.",
            )
        return user
    return _dependency
