"""
auth_endpoints.py — landing-page contact form + contact-message admin
endpoints. Account signup/login/session are handled by Clerk (frontend
ClerkProvider + backend services/auth/clerk_auth.py), not here.
"""

import hmac
import logging

from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel, Field

from ..core.rate_limit import limiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


class ContactRequest(BaseModel):
    name: str = Field(..., max_length=200)
    email: str = Field(..., max_length=254)
    message: str = Field(..., max_length=5000)


def _require_admin(x_admin_key: str = Header(default="")):
    from ..core.config import settings
    if not settings.ADMIN_API_KEY or not hmac.compare_digest(x_admin_key or "", settings.ADMIN_API_KEY):
        raise HTTPException(status_code=404)  # 404, not 401 — don't reveal the endpoint exists


# ============================================================================
# NOTE: /contact and /admin/* below are still live — the contact form and its
# SQLite storage are a separate feature from user accounts. Account signup,
# login, session management, and password reset are now handled entirely by
# Clerk on the frontend (see frontend/src/main.jsx's ClerkProvider) — this
# backend never sees a password; it only ever verifies the Clerk-issued
# session token (see services/auth/clerk_auth.py + tier_gate.py). The old
# Postgres/Supabase account system (services/auth/db.py) is no longer
# imported anywhere and can be deleted along with its DATABASE_URL once
# you've confirmed nothing external still points at it.
# ============================================================================


@router.post("/contact", summary="Landing page contact form")
@limiter.limit("5/minute")
async def contact(req: ContactRequest, request: Request):
    from ..core.config import settings
    from ..services.messages import db as messages_db
    from ..services.reporting.email_sender import send_email

    # Save first, unconditionally — previously this only ever emailed the
    # message and threw it away entirely if send_email failed (which it
    # currently does: no working email provider is configured yet). Now
    # the message survives regardless of whether the notification email
    # goes out, and can be read back via GET /contact/messages.
    import html as _html
    html_body = f"""
    <div style="background:#0a0c0f;padding:32px;font-family:'Courier New',monospace;color:#fff;">
      <div style="max-width:480px;margin:0 auto;background:#0d1117;border:1px solid #2a3040;border-radius:8px;padding:24px;">
        <div style="letter-spacing:2px;color:#c9a86a;font-size:12px;margin-bottom:16px;">VAYU — CONTACT FORM</div>
        <div style="font-size:13px;color:#c7d0da;margin-bottom:6px;"><b>From:</b> {_html.escape(req.name)} &lt;{_html.escape(req.email)}&gt;</div>
        <div style="font-size:13px;color:#c7d0da;line-height:1.6;white-space:pre-wrap;margin-top:14px;border-top:1px solid #2a3040;padding-top:14px;">{_html.escape(req.message)}</div>
      </div>
    </div>
    """
    destination = settings.ADMIN_EMAIL or settings.SMTP_USER
    emailed = False
    if destination:
        emailed = await send_email(destination, f"VAYU contact form — {req.name}", html_body)
    messages_db.save_message(req.name, req.email, req.message, emailed)
    return {"sent": True, "emailed": emailed}


@router.get("/contact/messages", summary="[admin] List contact-form submissions")
async def list_contact_messages(x_admin_key: str = Header(default=""), unresponded_only: bool = False):
    _require_admin(x_admin_key)
    from ..services.messages import db as messages_db
    return {"messages": messages_db.list_messages(include_responded=not unresponded_only)}


@router.post("/contact/messages/{message_id}/responded", summary="[admin] Mark a contact message as responded")
async def mark_contact_message_responded(message_id: str, x_admin_key: str = Header(default="")):
    _require_admin(x_admin_key)
    from ..services.messages import db as messages_db
    if not messages_db.mark_responded(message_id, True):
        raise HTTPException(status_code=404, detail="No message with that id.")
    return {"ok": True}
