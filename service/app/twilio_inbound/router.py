"""Twilio inbound voice webhooks + voicemail (Phases D.4 + D.5).

When a human calls the agent's Twilio number:

  1. Twilio POSTs to /webhooks/twilio/voice
  2. We verify X-Twilio-Signature, return TwiML that:
       <Say> a brief greeting
       <Record> up to 60 seconds, action=/recording-complete
       <Say> fallback if no recording
  3. Caller speaks; recording ends on hangup, # key, or maxLength.
  4. Twilio POSTs to /webhooks/twilio/voice/recording-complete with
     RecordingUrl + RecordingDuration + RecordingSid + From/To/CallSid.
  5. We store metadata in Redis voicemail inbox + return final
     TwiML <Say>thank you</Say><Hangup/>.
  6. Agent polls /voice/voicemail?to=<number> via EPT auth.

Future:
  D.7 inbound voice with <Gather speech> for real-time dialog
  D.8 transcription via Twilio's <Record transcribe="true"> OR via
      a future windy-cloud transcription service
"""

from __future__ import annotations

import logging
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from app.auth.dependencies import require_passport
from app.auth.ept import PassportClaims
from app.cell_client import CellClient
from app.config import get_settings
from app.eternitas_client import EternitasClient
from app.twilio_inbound.signature import verify_twilio_signature
from app.twilio_inbound.voicemail import fetch_voicemails, store_voicemail

logger = logging.getLogger(__name__)

router = APIRouter(tags=["twilio-inbound"])

# Greeting played BEFORE the beep. Caller hears this, then beep, then
# can leave a message up to 60 seconds. # ends the recording early.
DEFAULT_GREETING = (
    "Hello. You have reached an automated agent. "
    "Please leave a message after the beep, and the agent will get back to you. "
    "Press pound when finished."
)

# Played if no recording is captured (caller hangs up before the beep).
NO_RECORDING_FALLBACK = "Sorry, no message was recorded. Goodbye."

# Played after a successful recording — confirms receipt + hangs up.
THANK_YOU_AFTER_RECORD = "Thank you for your message. Goodbye."


def _build_voice_inbound_twiml(
    greeting: str,
    recording_callback_url: str,
    max_seconds: int = 60,
) -> str:
    """Inline TwiML: greeting → <Record> → fallback. The action callback
    is the /recording-complete webhook we host."""
    safe_greeting = xml_escape(greeting)
    safe_fallback = xml_escape(NO_RECORDING_FALLBACK)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        f'<Say voice="alice">{safe_greeting}</Say>'
        f'<Record action="{recording_callback_url}" method="POST" '
        f'maxLength="{max_seconds}" finishOnKey="#" playBeep="true" />'
        f'<Say voice="alice">{safe_fallback}</Say>'
        '<Hangup/>'
        '</Response>'
    )


def _build_thank_you_twiml() -> str:
    """TwiML returned from /recording-complete — close out the call."""
    safe = xml_escape(THANK_YOU_AFTER_RECORD)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Say voice="alice">{safe}</Say><Hangup/></Response>'
    )


# Kept for backwards-compat with the D.4 unit test of the simple
# greeting+hangup builder. New voicemail-aware path uses the builder
# above.
def _build_voice_twiml(greeting: str = DEFAULT_GREETING) -> str:
    safe = xml_escape(greeting)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Say voice="alice">{safe}</Say><Hangup/></Response>'
    )


async def _resolve_owner(request: Request, number: str) -> str:
    """Resolve `number` → owner passport via the cell client, with soft
    fallback to settings.fallback_owner_passport. Centralized so all
    inbound handlers share the same fallback policy.

    Today the fallback IS the right answer in practice (one number, one
    passport, +17542772201 → ET26-WIND-Y000). Once per-agent numbers
    land, an unresolved `to` should drop the call instead of falling
    through; we'll harden that policy in C.6.b.
    """
    cell: CellClient | None = getattr(request.app.state, "cell_client", None)
    if cell is None or not cell.configured:
        return get_settings().fallback_owner_passport
    resolved = await cell.lookup_owner(number)
    return resolved or get_settings().fallback_owner_passport


def _verify_twilio_request(
    settings: Any,
    request: Request,
    params: dict[str, str],
    x_twilio_signature: str | None,
) -> None:
    """Shared signature verification — raises 403/503 on failure."""
    if not settings.twilio_auth_token:
        raise HTTPException(status_code=503, detail="Twilio inbound not configured")

    full_url = settings.twilio_webhook_base_url.rstrip("/") + str(request.url.path)
    if request.url.query:
        full_url += "?" + str(request.url.query)

    if not verify_twilio_signature(
        full_url, params, x_twilio_signature, settings.twilio_auth_token
    ):
        logger.warning(
            "Twilio signature mismatch (path=%s, sig=%s)",
            request.url.path, (x_twilio_signature or "<none>")[:20],
        )
        raise HTTPException(status_code=403, detail="Invalid X-Twilio-Signature")


# -------------------------------------------------------------------------
# /webhooks/twilio/voice  — initial call landing
# -------------------------------------------------------------------------


@router.post("/webhooks/twilio/voice", include_in_schema=False)
async def twilio_inbound_voice(
    request: Request,
    x_twilio_signature: str | None = Header(default=None),
) -> Response:
    """Initial inbound-call webhook. Returns greeting + <Record> TwiML
    (D.5). Pre-D.5 behavior was greeting + hangup with no recording —
    a stub. D.5 closes the loop the way T.3 closed the SMS receive side."""
    settings = get_settings()
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}

    _verify_twilio_request(settings, request, params, x_twilio_signature)

    call_sid = params.get("CallSid", "")
    from_ = params.get("From", "")
    to = params.get("To", "")

    # C.6 — resolve owner passport for the destination number. Soft-fall
    # back to the configured fallback so cell-api downtime doesn't take
    # inbound voice down with it. Just for logging here; the real
    # integrity event posts in /recording-complete (when we have a
    # voicemail to attribute).
    owner = await _resolve_owner(request, to)
    logger.info(
        "inbound voice call_sid=%s from=%s to=%s owner=%s",
        call_sid, from_[:6] + "...", to, owner,
    )

    callback_url = (
        settings.twilio_webhook_base_url.rstrip("/")
        + "/webhooks/twilio/voice/recording-complete"
    )
    twiml = _build_voice_inbound_twiml(
        DEFAULT_GREETING,
        recording_callback_url=callback_url,
        max_seconds=60,
    )
    return Response(content=twiml, media_type="application/xml")


# -------------------------------------------------------------------------
# /webhooks/twilio/voice/recording-complete — fired after <Record> ends
# -------------------------------------------------------------------------


@router.post("/webhooks/twilio/voice/recording-complete", include_in_schema=False)
async def twilio_voice_recording_complete(
    request: Request,
    x_twilio_signature: str | None = Header(default=None),
) -> Response:
    """Receive the recording-complete callback. Twilio sends:
       RecordingSid, RecordingUrl, RecordingDuration, RecordingStatus,
       From, To, CallSid (passed through from the parent <Record>)."""
    settings = get_settings()
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}

    _verify_twilio_request(settings, request, params, x_twilio_signature)

    recording_sid = params.get("RecordingSid", "")
    recording_url = params.get("RecordingUrl", "")
    duration = int(params.get("RecordingDuration", "0") or 0)
    from_ = params.get("From", "")
    to = params.get("To", "")
    call_sid = params.get("CallSid", "")

    # Privacy: never log RecordingUrl in plain logs. Metadata only.
    logger.info(
        "voicemail received call=%s recording=%s duration=%ds to=%s",
        call_sid, recording_sid, duration, to,
    )

    redis = getattr(request.app.state, "redis", None)
    owner_passport = await _resolve_owner(request, to)

    await store_voicemail(
        redis,
        to=to,
        payload={
            "recording_sid": recording_sid,
            "recording_url": recording_url,
            "duration_seconds": duration,
            "from": from_,
            "to": to,
            "call_sid": call_sid,
            "owner_passport": owner_passport,
        },
        max_size=settings.inbox_max_per_number,
    )

    # C.6 — post the integrity event under the resolved owner passport.
    # Best-effort: don't let an eternitas hiccup keep us from returning
    # TwiML to Twilio (would trigger a retry storm).
    eternitas: EternitasClient | None = getattr(request.app.state, "eternitas_client", None)
    if eternitas is not None and eternitas.configured:
        try:
            await eternitas.submit_integrity_event(
                passport=owner_passport,
                event_type="voicemail_received",
                dimension="reliability",
                delta_hint=1,
                source="windy-call",
                context={
                    "call_sid": call_sid,
                    "duration_seconds": duration,
                    "to": to,
                },
                idempotency_key=f"voicemail:{recording_sid}",
            )
        except Exception as e:
            logger.warning(
                "integrity event post failed for voicemail %s: %s", call_sid, e
            )

    return Response(content=_build_thank_you_twiml(), media_type="application/xml")


# -------------------------------------------------------------------------
# /voice/voicemail — agent-facing inbox poll (EPT-gated)
# -------------------------------------------------------------------------


class VoicemailListResponse(BaseModel):
    to: str
    count: int
    voicemails: list[dict]


@router.get("/voice/voicemail", response_model=VoicemailListResponse)
async def voicemail_inbox(
    request: Request,
    to: str = Query(..., min_length=8, max_length=20, description="The recipient (Twilio) number"),
    limit: int = Query(default=25, ge=1, le=100),
    claims: PassportClaims = Depends(require_passport),
) -> VoicemailListResponse:
    """Read voicemails left at `to`. EPT-gated. Until per-passport
    number routing (windy-num) lands, any authenticated agent can read
    any number's voicemail — fine while there's only one platform-owned
    number."""
    redis = getattr(request.app.state, "redis", None)
    msgs = await fetch_voicemails(redis, to=to, limit=limit)
    return VoicemailListResponse(to=to, count=len(msgs), voicemails=msgs)
