"""
email_sender.py — thin wrapper around Resend's REST API
(https://resend.com), used for the daily executive summary.

Resend chosen over alternatives for the same reason other free-tier
sources in this project were: genuinely card-free signup (email only),
with a real free tier (100 emails/day / 3,000/month at the time this
was written) rather than a trial that expires.

Two things need to be configured for this to actually deliver mail
(both fail gracefully — see send_email below — rather than crashing the
report job if unset):
  - RESEND_API_KEY: generated in the Resend dashboard after signup
  - REPORT_FROM_EMAIL: must be an address on a domain VERIFIED in
    Resend's dashboard (DNS records added there) — an unverified
    sending domain can only deliver to the account owner's own address,
    which would silently make every subscriber's email fail. This is a
    one-time setup step, same shape as this project's other keyed
    integrations (data.gov.in, SerpApi) needing a one-time key/config
    step before they do anything.
"""

import logging
from typing import Optional

import httpx

from ...core.config import settings

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"


async def send_email(to: str, subject: str, html_body: str) -> bool:
    api_key = settings.RESEND_API_KEY
    if not api_key:
        logger.warning(f"send_email: RESEND_API_KEY not configured, skipping send to {to}")
        return False

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
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            if resp.status_code >= 400:
                logger.warning(f"send_email: Resend returned {resp.status_code} for {to}: {resp.text[:300]}")
                return False
            return True
    except Exception as e:
        logger.warning(f"send_email: request failed for {to}: {type(e).__name__}: {e}")
        return False
