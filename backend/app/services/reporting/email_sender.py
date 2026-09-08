"""
email_sender.py — sends the daily executive summary two possible ways:

1. Resend's REST API (https://resend.com) — if RESEND_API_KEY is set.
   Better deliverability at scale, but REQUIRES a domain you own and
   verify with DNS records (SPF/DKIM) — it can't send from a gmail.com/
   outlook.com/etc. address you don't control, and without a verified
   domain it can only deliver to your own signup email. This is a real
   requirement of how Resend (and every domain-based transactional
   email API) proves you're not spoofing a domain — there's no way
   around it that doesn't involve a domain.

2. Plain SMTP via Gmail's own relay (smtp.gmail.com) — used automatically
   whenever RESEND_API_KEY isn't set but SMTP_USER/SMTP_PASSWORD are.
   Needs no domain at all: any Gmail account works, using an "app
   password" (not your normal Google login password) generated at
   myaccount.google.com/apppasswords (needs 2-Step Verification turned
   on first, which is a Google requirement for app passwords, not
   something this code imposes). Genuinely free, no card, no domain —
   the actual workaround for not having a domain to give Resend.
   Tradeoffs, stated plainly rather than glossed over: Gmail's free-tier
   sending cap is around 500 messages/day, mail arrives "from" a
   personal-looking Gmail address rather than a branded one, and Google
   can throttle or flag automated sending patterns over time. Fine for
   this project's current subscriber scale; a real domain + Resend is
   the better long-term answer once one exists.

smtplib is synchronous (stdlib has no async SMTP client) — wrapped in
asyncio.to_thread so it doesn't block the event loop while the daily
report is sending to a whole subscriber list, matching how this
project already wraps other blocking calls (GEE compute, etc.).
"""

import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

from ...core.config import settings

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"


async def send_email(to: str, subject: str, html_body: str) -> bool:
    if settings.RESEND_API_KEY:
        return await _send_via_resend(to, subject, html_body)
    if settings.SMTP_USER and settings.SMTP_PASSWORD:
        return await asyncio.to_thread(_send_via_smtp, to, subject, html_body)
    logger.warning(f"send_email: no email provider configured (neither RESEND_API_KEY nor SMTP_USER/SMTP_PASSWORD), skipping send to {to}")
    return False


async def _send_via_resend(to: str, subject: str, html_body: str) -> bool:
    payload = {
        "from": settings.REPORT_FROM_EMAIL,
        "to": [to],
        "subject": subject,
        "html": html_body,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                RESEND_URL,
                json=payload,
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}", "Content-Type": "application/json"},
            )
            if resp.status_code >= 400:
                logger.warning(f"send_email (Resend): {resp.status_code} for {to}: {resp.text[:300]}")
                return False
            return True
    except Exception as e:
        logger.warning(f"send_email (Resend): request failed for {to}: {type(e).__name__}: {e}")
        return False


def _send_via_smtp(to: str, subject: str, html_body: str) -> bool:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_USER
    msg["To"] = to
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_USER, [to], msg.as_string())
        return True
    except smtplib.SMTPAuthenticationError as e:
        # By far the most common failure here: SMTP_PASSWORD is the
        # normal Google account password instead of a generated app
        # password — Gmail rejects those outright for SMTP login.
        logger.warning(f"send_email (SMTP): auth failed for {settings.SMTP_USER} — is SMTP_PASSWORD a Gmail APP PASSWORD, not the account login password? {e}")
        return False
    except Exception as e:
        logger.warning(f"send_email (SMTP): failed for {to}: {type(e).__name__}: {e}")
        return False
