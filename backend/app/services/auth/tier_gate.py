"""
auth/tier_gate.py — reusable FastAPI dependencies for (a) resolving the
logged-in user from their session token, and (b) requiring a minimum
service tier before an endpoint runs.

Deliberately NOT attached to any endpoint yet. This is the mechanism
for the free/agri/business/full paid-tier plan, built and working, but
wiring it onto real data endpoints (vessels, aircraft, commodities,
agri, ...) also requires every frontend hook that calls them to start
sending the session token — a wider change than "add a dependency" and
one that shouldn't happen silently in the same pass as the auth
migration. Wire it up deliberately, endpoint by endpoint, once the
tier-to-feature mapping is decided.

Why this and not just trusting the frontend: a frontend that decides
"don't render the Maritime panel for a free user" is a UI choice, not
security — anyone can still call the API endpoint directly with their
own token and get the data regardless of what's rendered. The check
below runs server-side, per request, against the token's actual owner,
so hiding a panel and enforcing access are two independent things —
this file is only the second one.
"""

from fastapi import Header, HTTPException

from . import db as auth_db


async def get_current_user(authorization: str = Header(default="")) -> dict:
    """Resolves the bearer token to a user dict ({id, name, email,
    service_tier}), or raises 401. Use this alone (without a tier
    requirement) on any endpoint that just needs "must be logged in,
    tier doesn't matter"."""
    token = authorization.replace("Bearer ", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Not logged in.")
    try:
        user = auth_db.get_user_for_token(token)
    except auth_db.AuthDBUnavailable:
        raise HTTPException(status_code=503, detail="Auth service is temporarily unavailable.")
    if not user:
        raise HTTPException(status_code=401, detail="Session expired or invalid.")
    return user


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
