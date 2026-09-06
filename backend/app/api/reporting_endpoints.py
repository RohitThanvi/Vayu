"""
reporting_endpoints.py — the daily executive summary's public surface:
subscribe, unsubscribe, and a manual trigger for testing the pipeline
without waiting for the daily clock.
"""

import html
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..core.rate_limit import limiter
from fastapi import Request

from ..services.reporting import subscribers as sub_store
from ..services.reporting.report_job import run_daily_report

logger = logging.getLogger(__name__)
router = APIRouter(tags=["reporting"])


class SubscribeRequest(BaseModel):
    email: str = Field(..., max_length=254)


@router.post("/subscribe", summary="Subscribe to the daily executive summary email")
@limiter.limit("5/minute")
async def subscribe(req: SubscribeRequest, request: Request):
    email = req.email.strip()
    if not sub_store.is_valid_email(email):
        raise HTTPException(status_code=422, detail="That doesn't look like a valid email address.")
    result = sub_store.add_subscriber(email)
    return {
        "subscribed": True,
        "already_subscribed": result["already_subscribed"],
        "message": "You're already on the list." if result["already_subscribed"] else "Subscribed — your first briefing arrives at the next daily send.",
    }


@router.get("/unsubscribe", response_class=HTMLResponse, summary="One-click unsubscribe (link sent in every report email)")
async def unsubscribe(token: str):
    ok = sub_store.unsubscribe(token)
    # A plain, self-contained confirmation page — this is reached by
    # clicking a link in an email client's browser view, not the app
    # itself, so it can't assume the SPA or any app styling is loaded.
    message = "You've been unsubscribed from the VAYU daily briefing." if ok else "This unsubscribe link is invalid or already used."
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>VAYU</title></head>
    <body style="background:#0a0c0f; color:#fff; font-family:'Courier New',monospace; display:flex; align-items:center; justify-content:center; height:100vh; margin:0;">
      <div style="text-align:center; max-width:400px; padding:20px;">
        <div style="letter-spacing:3px; font-size:14px; color:#c9a86a; margin-bottom:16px;">VAYU</div>
        <div style="font-size:14px; line-height:1.6;">{html.escape(message)}</div>
      </div>
    </body></html>"""


@router.post("/reporting/send-now", summary="Manually trigger the daily report immediately (for testing)")
@limiter.limit("2/minute")
async def send_now(request: Request):
    result = await run_daily_report()
    return result
