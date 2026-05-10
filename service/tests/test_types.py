"""Round-trip tests for canonical V1 wire-protocol types (M0.5).

Pins the contract shapes that M3 `/v1/call/*` endpoints will adopt.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.types import (
    CallEvent,
    CallEventType,
    CallRequest,
    Recording,
    RecordingConsent,
    VoicePersona,
)


def test_call_request_defaults_round_trip():
    req = CallRequest(to="+15551234567", agent_brief="Reschedule the dental appointment.")
    blob = req.model_dump_json()
    assert CallRequest.model_validate_json(blob) == req
    # defaults documented in master plan §5
    assert req.voice_persona == VoicePersona.WARM_FEMALE_30S
    assert req.max_duration_sec == 300
    assert req.max_cost_usd == 1.50
    assert req.recording_consent == RecordingConsent.OPT_IN_PENDING
    assert req.ai_disclosure_required is True


def test_call_request_rejects_non_e164():
    with pytest.raises(ValidationError):
        CallRequest(to="555-123-4567", agent_brief="hi")


def test_call_request_rejects_blank_brief():
    with pytest.raises(ValidationError):
        CallRequest(to="+15551234567", agent_brief="")


def test_call_event_round_trip():
    ev = CallEvent(
        id="evt_abc",
        type=CallEventType.RINGING,
        timestamp=datetime(2026, 5, 10, tzinfo=UTC),
        payload={"trunk": "twilio-us-east"},
    )
    assert CallEvent.model_validate_json(ev.model_dump_json()) == ev


def test_recording_round_trip():
    r = Recording(
        id="rec_xyz",
        call_id="call_abc",
        s3_url="s3://windy-call-recordings/rec_xyz.mp3",
        ept_signature="eyJhbGciOiJFUzI1NiIs...",
        recorded_at=datetime(2026, 5, 10, tzinfo=UTC),
    )
    assert Recording.model_validate_json(r.model_dump_json()) == r


def test_cost_cap_must_be_positive():
    with pytest.raises(ValidationError):
        CallRequest(to="+15551234567", agent_brief="hi", max_cost_usd=0.0)
