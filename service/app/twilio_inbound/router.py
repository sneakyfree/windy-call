"""Twilio inbound voice webhook (Phase D.4).

When a human calls the agent's Twilio number, Twilio POSTs here. We
verify the Twilio signature, return TwiML that drives the call, and
post an integrity event so the audit trail captures every inbound
attempt.

v1 (this codon): polite "agent unavailable" greeting that points
caller to SMS, then hangs up. Confirms the number is live, gives a
clear next-action.

v2 (D.5): <Record> the caller's voicemail, transcribe via Twilio
(or a future windy-cloud transcription service), drop in the agent's
inbox alongside SMS messages.

v3 (D.7): <Gather speech> the caller's intent, route to the agent's
runtime for a real-time spoken dialogue.
"""

from __future__ import annotations

import logging
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response

from app.config import get_settings
from app.twilio_inbound.signature import verify_twilio_signature

logger = logging.getLogger(__name__)

router = APIRouter(tags=["twilio-inbound"])

# Default v1 greeting for inbound voice. Polite + actionable. The agent
# is mostly contactable via SMS/text in v1; this is the bridge from
# voice-callers (humans who default to dialing).
DEFAULT_GREETING = (
    "Hello. You have reached an automated agent. "
    "The agent prefers text messages. "
    "Please send a text to this number and the agent will respond shortly. "
    "Goodbye."
)


def _build_voice_twiml(greeting: str = DEFAULT_GREETING) -> str:
    """Inline TwiML: <Say>greeting</Say><Hangup/>."""
    safe = xml_escape(greeting)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Say voice="alice">{safe}</Say><Hangup/></Response>'
    )


@router.post("/webhooks/twilio/voice", include_in_schema=False)
async def twilio_inbound_voice(
    request: Request,
    x_twilio_signature: str | None = Header(default=None),
) -> Response:
    """Receive an inbound voice call from Twilio.

    Verifies X-Twilio-Signature, returns TwiML that drives the call.
    On signature mismatch we return 403 — Twilio will retry, but a
    persistent mismatch usually means misconfigured webhook URL or
    rotated auth token.

    Failure modes:
      - 503: Twilio not configured (no auth token to verify with)
      - 403: bad signature
      - 200: TwiML response (always, on success)
    """
    settings = get_settings()
    if not settings.twilio_auth_token:
        raise HTTPException(status_code=503, detail="Twilio inbound not configured")

    form = await request.form()
    params = {k: str(v) for k, v in form.items()}

    full_url = settings.twilio_webhook_base_url.rstrip("/") + str(request.url.path)
    if request.url.query:
        full_url += "?" + str(request.url.query)

    if not verify_twilio_signature(
        full_url, params, x_twilio_signature, settings.twilio_auth_token
    ):
        logger.warning(
            "Twilio signature mismatch on inbound voice (sid=%s, from=%s, url=%s)",
            params.get("CallSid", "?")[:34],
            params.get("From", "?")[:6] + "...",
            full_url,
        )
        raise HTTPException(status_code=403, detail="Invalid X-Twilio-Signature")

    call_sid = params.get("CallSid", "")
    from_ = params.get("From", "")
    to = params.get("To", "")
    call_status = params.get("CallStatus", "")

    # Privacy: log call metadata, never log voice content.
    logger.info(
        "inbound voice call_sid=%s from=%s to=%s status=%s",
        call_sid, from_[:6] + "...", to, call_status,
    )

    # Best-effort integrity event — same caveat as T.3 inbound SMS:
    # without a number-to-passport registry we can't credit the right
    # agent yet. Future codon (consume windy-num).

    twiml = _build_voice_twiml()
    return Response(content=twiml, media_type="application/xml")
