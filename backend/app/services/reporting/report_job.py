"""
report_job.py — the actual daily job: gather → summarize once → render
once → send to every active subscriber. Called by the scheduler at
DAILY_REPORT_TIME, and also exposed as a manually-triggerable function
(see api/reporting_endpoints.py's /reporting/send-now) for testing
without waiting for the clock.
"""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from ...core.config import settings
from . import subscribers as sub_store
from .aggregator import gather_snapshot
from .summarizer import generate_summary
from .email_render import render_report_html
from .email_sender import send_email

logger = logging.getLogger(__name__)


async def run_daily_report() -> dict:
    subscribers = sub_store.get_active_subscribers()
    if not subscribers:
        logger.info("run_daily_report: no active subscribers, skipping")
        return {"sent": 0, "failed": 0, "subscriber_count": 0}

    # Gather + summarize ONCE — every subscriber gets the identical
    # report (see summarizer.py docstring for why this isn't per-user).
    snapshot = gather_snapshot()
    summary = generate_summary(snapshot)

    tz = ZoneInfo(settings.REPORT_TIMEZONE)
    report_date = datetime.now(tz).strftime("%B %d, %Y")
    subject = f"VAYU Daily Briefing — {summary.get('headline', 'Executive Summary')}"[:150]

    sent, failed = 0, 0
    for s in subscribers:
        unsubscribe_url = f"{_backend_base_url()}/api/v1/unsubscribe?token={s['unsubscribe_token']}"
        html_body = render_report_html(summary, snapshot, unsubscribe_url, settings.FRONTEND_URL, report_date)
        ok = await send_email(s["email"], subject, html_body)
        if ok:
            sent += 1
        else:
            failed += 1
        # A short pause between sends is a courtesy to Resend's rate
        # limits, not a hard requirement at this project's subscriber
        # scale — cheap insurance if the list ever grows past a trickle.
        await asyncio.sleep(0.3)

    logger.info(f"run_daily_report: sent={sent} failed={failed} of {len(subscribers)} subscribers")
    return {"sent": sent, "failed": failed, "subscriber_count": len(subscribers)}


def _backend_base_url() -> str:
    # The unsubscribe link must point at THIS backend (it's a backend
    # route, /api/v1/unsubscribe), not the frontend — no existing config
    # var holds the backend's own public URL, so this is deliberately
    # narrow rather than adding a new required setting: Render sets
    # RENDER_EXTERNAL_URL automatically for every web service, which is
    # exactly this backend's own public URL.
    import os
    return os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000").rstrip("/")
