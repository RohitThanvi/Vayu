"""
auth_endpoints.py — signup/login/session-check/logout for the landing-
page gate. See services/auth/db.py for the storage/crypto details.
"""

import hmac
import logging

from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel, Field

from ..core.rate_limit import limiter
from ..services.auth import db as auth_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=8, max_length=200)


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., max_length=200)


class ContactRequest(BaseModel):
    name: str = Field(..., max_length=200)
    email: str = Field(..., max_length=254)
    message: str = Field(..., max_length=5000)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., max_length=254)


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8, max_length=200)


def _auth_response(user: dict) -> dict:
    token = auth_db.create_session(user["id"])
    return {"token": token, "email": user["email"]}


@router.post("/signup", summary="Create an account")
@limiter.limit("5/minute")
async def signup(req: SignupRequest, request: Request):
    if not auth_db.is_valid_email(req.email):
        raise HTTPException(status_code=422, detail="That doesn't look like a valid email address.")
    user = auth_db.create_user(req.email, req.password)
    if not user:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    return _auth_response(user)


@router.post("/login", summary="Log in")
@limiter.limit("10/minute")
async def login(req: LoginRequest, request: Request):
    user = auth_db.authenticate(req.email, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    return _auth_response(user)


@router.get("/me", summary="Check the current session")
async def me(authorization: str = Header(default="")):
    token = authorization.replace("Bearer ", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Not logged in.")
    user = auth_db.get_user_for_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Session expired or invalid.")
    return {"email": user["email"]}


@router.post("/logout", summary="Log out")
async def logout(authorization: str = Header(default="")):
    token = authorization.replace("Bearer ", "").strip()
    if token:
        auth_db.delete_session(token)
    return {"logged_out": True}


@router.post("/forgot-password", summary="Request a password reset email")
@limiter.limit("3/minute")
async def forgot_password(req: ForgotPasswordRequest, request: Request):
    from ..core.config import settings
    from ..services.reporting.email_sender import send_email

    # Always return the same response whether or not the email exists —
    # revealing "that account doesn't exist" from this endpoint would let
    # anyone enumerate registered emails one guess at a time.
    user = auth_db.get_user_by_email(req.email)
    if user:
        token = auth_db.create_password_reset(user["id"])
        reset_url = f"{settings.FRONTEND_URL}/?reset_token={token}"
        html_body = f"""
        <div style="background:#0a0c0f;padding:32px;font-family:'Courier New',monospace;color:#fff;">
          <div style="max-width:440px;margin:0 auto;background:#0d1117;border:1px solid #2a3040;border-radius:8px;padding:28px;">
            <div style="letter-spacing:3px;color:#c9a86a;font-size:14px;margin-bottom:18px;">VAYU</div>
            <div style="font-size:14px;line-height:1.6;color:#c7d0da;margin-bottom:20px;">
              Someone requested a password reset for this email. If that was you, click below —
              this link expires in {auth_db.PASSWORD_RESET_TTL_MINUTES} minutes. If it wasn't you, you can ignore this.
            </div>
            <a href="{reset_url}" style="display:block;text-align:center;background:linear-gradient(180deg,#f5d98a,#c9a86a);color:#05070c;font-weight:bold;letter-spacing:1px;text-decoration:none;padding:12px;border-radius:4px;">
              RESET PASSWORD
            </a>
          </div>
        </div>
        """
        await send_email(user["email"], "VAYU — Reset your password", html_body)
    return {"message": "If that email is registered, a reset link has been sent."}


@router.post("/reset-password", summary="Complete a password reset")
@limiter.limit("5/minute")
async def reset_password(req: ResetPasswordRequest, request: Request):
    ok = auth_db.consume_password_reset(req.token, req.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired.")
    return {"reset": True}


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


def _require_admin(x_admin_key: str = Header(default="")):
    from ..core.config import settings
    if not settings.ADMIN_API_KEY or not hmac.compare_digest(x_admin_key or "", settings.ADMIN_API_KEY):
        raise HTTPException(status_code=404)  # 404, not 401 — don't reveal the endpoint exists


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
