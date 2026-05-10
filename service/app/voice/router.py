"""Voice endpoints (Phase D.3 — outbound TTS-driven calls).

The agent provides a phone number and a message; we call the number,
Twilio's TTS reads the message via <Say>, the call hangs up. v1 is
"voice notification" — the agent reaches a human on a number to
deliver a brief spoken update.

Future codons add:
  D.4  inbound voice webhook (Twilio → /webhooks/twilio/voice + TwiML)
  D.5  voicemail recording + transcription
  D.6  number-to-passport registry (consume future windy-num)
  D.7  spam-report + auto-suspension
  D.8  Voice SDK / WebRTC for in-browser calls
"""

from __future__ import annotations

import logging
import re
import uuid
from xml.sax.saxutils import escape as xml_escape

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.dependencies import require_passport_with_cost_cap
from app.auth.ept import PassportClaims
from app.eternitas_client import EternitasClient
from app.twilio_client import TwilioClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])

# E.164 destination phone number format.
_E164_RE = re.compile(r"^\+[1-9]\d{1,14}$")

# Hard cap on TTS message length. Twilio <Say> is fine with long text but
# longer messages → longer (more expensive) calls. 500 chars ≈ 30s of TTS.
MAX_MESSAGE_CHARS = 500

# Available <Say voice="..."> options. Stick to the basic set for now;
# Twilio Polly + neural voices are paid features per region.
VALID_VOICES = ("alice", "man", "woman")


class CreateCallRequest(BaseModel):
    to: str = Field(..., min_length=8, max_length=20, description="E.164 destination")
    message: str = Field(..., min_length=1, max_length=MAX_MESSAGE_CHARS,
                         description="Text the agent wants spoken on the call")
    voice: str = Field(default="alice", description="TTS voice")
    from_number: str | None = Field(
        default=None,
        alias="from",
        description="Override platform-default sender (must be a Twilio number you own)",
    )

    model_config = {"populate_by_name": True}


class CreateCallResponse(BaseModel):
    sid: str
    status: str
    to: str
    from_number: str = Field(..., alias="from")
    integrity_event_posted: bool

    model_config = {"populate_by_name": True}


def _build_twiml(message: str, voice: str) -> str:
    """Inline TwiML: <Response><Say voice=…>message</Say></Response>.

    XML-escapes the message so a payload like 'tag <Pause/>' doesn't get
    interpreted as nested TwiML. Voice is allow-listed.
    """
    safe_voice = voice if voice in VALID_VOICES else "alice"
    safe_message = xml_escape(message)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Say voice="{safe_voice}">{safe_message}</Say></Response>'
    )


@router.post("/call", response_model=CreateCallResponse)
async def create_voice_call(
    body: CreateCallRequest,
    request: Request,
    claims: PassportClaims = Depends(require_passport_with_cost_cap("voice.call")),
) -> CreateCallResponse:
    """Place an outbound voice call. The agent's `message` is read aloud
    by Twilio TTS, then the call hangs up.

    Trial-account constraints:
      - `to` must be a Twilio-verified number (otherwise 21219 from Twilio)
      - Voice doesn't need A2P 10DLC (that's SMS-only)

    Failure mapping:
      - 503: Twilio not configured
      - 400: bad E.164 / Twilio caller-fault error
      - 502: Twilio API failure (network or upstream 5xx)
    """
    twilio: TwilioClient | None = getattr(request.app.state, "twilio_client", None)
    if twilio is None or not twilio.configured:
        raise HTTPException(status_code=503, detail="Twilio client not configured")

    if not _E164_RE.match(body.to):
        raise HTTPException(
            status_code=400,
            detail=f"`to` must be E.164 (+countrycode + digits), got {body.to!r}",
        )

    twiml = _build_twiml(body.message, body.voice)

    try:
        result = await twilio.create_call(
            to=body.to,
            twiml=twiml,
            from_number=body.from_number,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except httpx.HTTPStatusError as e:
        try:
            err = e.response.json()
            detail = f"twilio:{err.get('code')}: {err.get('message', e.response.text[:200])}"
        except (ValueError, KeyError):
            detail = f"twilio HTTP {e.response.status_code}"
        status = 400 if 400 <= e.response.status_code < 500 else 502
        raise HTTPException(status_code=status, detail=detail)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"twilio network: {e}")

    eternitas: EternitasClient | None = getattr(request.app.state, "eternitas_client", None)
    posted = False
    if eternitas is not None:
        idem = f"voice:{claims.passport}:{uuid.uuid4().hex}"
        post_resp = await eternitas.submit_integrity_event(
            passport=claims.passport,
            event_type="voice.call.completed",
            dimension="reliability",
            delta_hint=1,
            source="windy-call",
            context={
                "to_country": body.to[:3],  # +country prefix only; never log full PII
                "twilio_status": result.status,
                "message_chars": len(body.message),
                "voice": body.voice if body.voice in VALID_VOICES else "alice",
            },
            idempotency_key=idem,
        )
        posted = post_resp is not None

    return CreateCallResponse(
        sid=result.sid,
        status=result.status,
        to=result.to,
        from_number=result.from_,
        integrity_event_posted=posted,
    )
