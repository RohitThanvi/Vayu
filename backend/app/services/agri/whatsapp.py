"""
whatsapp.py — WhatsApp last-mile delivery, built on Twilio's WhatsApp API.

Two directions:
1. Inbound: a farmer/officer texts a place name ("Churu") to the bot number;
   we geocode it (reusing the same Nominatim geocoding the frontend search
   bar uses), run the risk score against that boundary, and reply in plain
   language.
2. Outbound: alert_engine pushes a message when a watched region crosses
   its threshold (send_whatsapp_message, used as the alert_engine's
   whatsapp_notify callback).

Deliberately built on raw httpx + hand-built TwiML rather than the `twilio`
SDK — avoids a new dependency for what's a handful of REST calls, and
httpx is already in requirements.txt.

Requires env vars to actually send (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
TWILIO_WHATSAPP_FROM, e.g. "whatsapp:+14155238886" for the Twilio sandbox).
Inbound parsing/reply works even without outbound creds configured, since
Twilio calls the webhook and expects a TwiML response either way.
"""

import logging
import os
from typing import Optional
from xml.sax.saxutils import escape

import httpx

logger = logging.getLogger(__name__)

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "")


def build_twiml_reply(message: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{escape(message)}</Message></Response>'


async def geocode_place(query: str) -> Optional[dict]:
    """Nominatim geocode + boundary — same service the frontend search bar uses."""
    try:
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "VAYU-Agri/1.0"}) as client:
            resp = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": query, "format": "geojson", "polygon_geojson": 1, "limit": 1},
            )
            resp.raise_for_status()
            data = resp.json()
            features = data.get("features", [])
            if not features:
                return None
            return features[0]
    except Exception as e:
        logger.warning(f"geocode_place failed for '{query}': {e}")
        return None


async def handle_inbound_message(body_text: str) -> str:
    """Returns the plain-language reply text (caller wraps it in TwiML)."""
    from .risk_scoring import compute_risk_score

    place = (body_text or "").strip()
    if not place:
        return "Send a place name (e.g. 'Churu') to get its current agricultural risk score."

    feature = await geocode_place(place)
    if not feature or feature.get("geometry") is None:
        return f"Couldn't find a boundary for '{place}'. Try a district or block name."

    try:
        result = compute_risk_score(aoi=feature["geometry"])
    except Exception as e:
        logger.warning(f"whatsapp risk score failed for '{place}': {e}")
        return f"Found '{place}' but the risk analysis failed right now — please try again shortly."

    return (
        f"{place.title()} — Risk: {result['band'].upper()} ({result['risk_score']}/100)\n"
        f"Confidence: {result['confidence']}%\n"
        f"{result['reason']}"
    )


async def send_whatsapp_message(to_phone: str, message: str):
    """Outbound push, used by alert_engine. No-ops with a warning if Twilio creds aren't set."""
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM):
        logger.warning("send_whatsapp_message: Twilio credentials not configured, skipping send")
        return
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    to = to_phone if to_phone.startswith("whatsapp:") else f"whatsapp:{to_phone}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            url,
            data={"From": TWILIO_WHATSAPP_FROM, "To": to, "Body": message},
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        )
        resp.raise_for_status()
