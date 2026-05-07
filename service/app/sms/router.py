"""SMS endpoints (Phase D.1 — outbound only; inbound webhooks land later)."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.dependencies import require_passport
from app.auth.ept import PassportClaims
from app.eternitas_client import EternitasClient
from app.twilio_client import TwilioClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sms", tags=["sms"])

# E.164 phone number format: + followed by up to 15 digits.
# (Looser than full E.164 spec — Twilio handles country-code validation.)
_E164_RE = re.compile(r"^\+[1-9]\d{1,14}$")

# Hard cap on SMS body — long messages segment into multiple billed
# parts, which an agent shouldn't be able to do unintentionally. 1600
# is Twilio's stated max for concatenated SMS; we cap at 320 (≈2 segs)
# until per-tier policy lands.
MAX_SMS_BODY = 320


class SendSMSRequest(BaseModel):
    to: str = Field(..., min_length=8, max_length=20, description="E.164 destination, e.g. +15551234567")
    body: str = Field(..., min_length=1, max_length=MAX_SMS_BODY)
    from_number: str | None = Field(
        default=None,
        alias="from",
        description="Override the platform-default sender (must be a Twilio number you own).",
    )

    class Config:
        populate_by_name = True


class SendSMSResponse(BaseModel):
    sid: str
    status: str
    to: str
    from_number: str = Field(..., alias="from")
    integrity_event_posted: bool

    class Config:
        populate_by_name = True


@router.post("/send", response_model=SendSMSResponse)
async def send_sms(
    body: SendSMSRequest,
    request: Request,
    claims: PassportClaims = Depends(require_passport),
) -> SendSMSResponse:
    """Send an outbound SMS.

    Trial-account constraints (visible to agents):
      - `to` must be a Twilio-verified number (otherwise 21608 from Twilio)
      - All messages prefixed with "Sent from your Twilio trial account"

    Failure mapping:
      - 503: Twilio not configured
      - 422: bad E.164 format (caught at schema layer; this 400 is a
             defense-in-depth re-check)
      - 502: Twilio API failure (network or upstream 5xx)
      - 4xx with Twilio's specific error_code passed through
    """
    twilio: TwilioClient | None = getattr(request.app.state, "twilio_client", None)
    if twilio is None or not twilio.configured:
        raise HTTPException(status_code=503, detail="Twilio client not configured")

    if not _E164_RE.match(body.to):
        raise HTTPException(
            status_code=400,
            detail=f"`to` must be E.164 (+countrycode + digits), got {body.to!r}",
        )

    try:
        result = await twilio.send_sms(to=body.to, body=body.body, from_number=body.from_number)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except httpx.HTTPStatusError as e:
        # Twilio puts a structured error in the body — surface it.
        try:
            err = e.response.json()
            twilio_code = err.get("code")
            twilio_msg = err.get("message", e.response.text[:200])
            detail = f"twilio:{twilio_code}: {twilio_msg}"
        except (ValueError, KeyError):
            detail = f"twilio HTTP {e.response.status_code}"
        # Map Twilio errors: 400-class is caller's fault, 5xx is upstream
        status = 400 if 400 <= e.response.status_code < 500 else 502
        raise HTTPException(status_code=status, detail=detail)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"twilio network: {e}")

    eternitas: Optional[EternitasClient] = getattr(request.app.state, "eternitas_client", None)
    posted = False
    if eternitas is not None:
        idem = f"sms:{claims.passport}:{uuid.uuid4().hex}"
        post_resp = await eternitas.submit_integrity_event(
            passport=claims.passport,
            event_type="sms.send.completed",
            dimension="reliability",
            delta_hint=1,
            source="windy-call",
            context={
                "to_country": body.to[:3],  # +country prefix only — never log full PII
                "twilio_status": result.status,
                "body_chars": len(body.body),
            },
            idempotency_key=idem,
        )
        posted = post_resp is not None

    return SendSMSResponse(
        sid=result.sid,
        status=result.status,
        to=result.to,
        from_number=result.from_,
        integrity_event_posted=posted,
    )
