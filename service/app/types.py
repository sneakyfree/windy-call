"""Canonical V1 wire-protocol types for Windy Call.

Per [Windy Triad master plan §5](https://github.com/sneakyfree/kit-army-config/blob/main/docs/windy-triad-master-plan-2026-05-10.md)
and ADR-013 (Python+FastAPI canonical). These are the agent-facing
contract Call will expose at `/v1/call/*` as M3 lands.

Coexists with the current `/voice/*` and Twilio-webhook schemas until
M3 routes adopt these shapes.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class VoicePersona(StrEnum):
    """V1 predefined voice personas. Expandable via Windy Clone integration in V2+."""

    WARM_FEMALE_30S = "warm_female_30s"
    WARM_MALE_30S = "warm_male_30s"
    NEUTRAL_FEMALE_40S = "neutral_female_40s"
    NEUTRAL_MALE_40S = "neutral_male_40s"
    PROFESSIONAL_FEMALE = "professional_female"
    PROFESSIONAL_MALE = "professional_male"


class CallEventType(StrEnum):
    """Call lifecycle events streamed via `/v1/call/{call_id}/events`."""

    RINGING = "ringing"
    ANSWERED = "answered"
    IN_CONVERSATION = "in_conversation"
    ENDED = "ended"
    RECORDING_AVAILABLE = "recording_available"


class RecordingConsent(StrEnum):
    """Recording-consent posture for a call."""

    NOT_REQUIRED = "not_required"
    OPT_IN_PENDING = "opt_in_pending"
    GRANTED = "granted"
    DENIED = "denied"


class CallRequest(BaseModel):
    """Wire shape for `POST /v1/call/initiate` per master plan §5."""

    to: str = Field(..., pattern=r"^\+\d{10,15}$", description="E.164 recipient number")
    agent_brief: str = Field(..., min_length=1, max_length=2000)
    voice_persona: VoicePersona = VoicePersona.WARM_FEMALE_30S
    max_duration_sec: int = Field(default=300, ge=10, le=3600)
    max_cost_usd: float = Field(default=1.50, gt=0.0, le=100.0)
    recording_consent: RecordingConsent = RecordingConsent.OPT_IN_PENDING
    ai_disclosure_required: bool = True


class CallEvent(BaseModel):
    """A single lifecycle event on an in-flight call."""

    id: str
    type: CallEventType
    timestamp: datetime
    payload: dict | None = None


class Recording(BaseModel):
    """An EPT-signed call recording. Storage is S3 (Windy Cloud) per master plan §6 M6.5."""

    id: str
    call_id: str
    s3_url: str
    ept_signature: str
    recorded_at: datetime
