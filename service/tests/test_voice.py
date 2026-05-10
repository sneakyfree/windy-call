"""D.3 — /voice/call endpoint tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from tests.auth_helpers import sign_test_ept

# ---- TwiML builder unit -----------------------------------------


def test_twiml_escapes_xml_special_chars():
    """Caller-supplied message must not be able to inject TwiML tags.

    `<` and `&` are escaped (those are the actual injection vectors).
    Apostrophes pass through; they're safe inside `<Say>` text content
    (only attribute-context apostrophes need escaping, and we control
    the attribute string ourselves).
    """
    from app.voice.router import _build_twiml

    twiml = _build_twiml("hello <Pause length='10'/> & 'world'", "alice")
    # The injected tag's `<` is escaped — Twilio sees it as text, not nested TwiML
    assert "<Pause length=" not in twiml
    assert "&lt;Pause" in twiml
    assert "&amp;" in twiml


def test_twiml_clamps_invalid_voice_to_alice():
    """Voice allow-list defends against caller-supplied attribute injection."""
    from app.voice.router import _build_twiml

    twiml = _build_twiml("hi", "evil-voice")
    assert 'voice="alice"' in twiml
    assert "evil-voice" not in twiml


def test_twiml_accepts_valid_voices():
    from app.voice.router import _build_twiml

    for v in ("alice", "man", "woman"):
        twiml = _build_twiml("hi", v)
        assert f'voice="{v}"' in twiml


# ---- /voice/call endpoint ---------------------------------------


@pytest.mark.asyncio
async def test_voice_requires_authorization(auth_client):
    resp = await auth_client.post("/voice/call", json={"to": "+15551234567", "message": "x"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_voice_rejects_non_e164(auth_client, ept_keypair):
    token = sign_test_ept(ept_keypair)
    resp = await auth_client.post(
        "/voice/call",
        headers={"Authorization": f"Bearer {token}"},
        json={"to": "5551234567", "message": "hello"},
    )
    assert resp.status_code == 400
    assert "E.164" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_voice_rejects_oversize_message(auth_client, ept_keypair):
    token = sign_test_ept(ept_keypair)
    resp = await auth_client.post(
        "/voice/call",
        headers={"Authorization": f"Bearer {token}"},
        json={"to": "+15551234567", "message": "x" * 600},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_voice_happy_path_posts_event(auth_client, ept_keypair):
    from app.main import app

    token = sign_test_ept(ept_keypair, passport="ET26-VOIC-AAAA")
    resp = await auth_client.post(
        "/voice/call",
        headers={"Authorization": f"Bearer {token}"},
        json={"to": "+15551234567", "message": "Your agent has news."},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["sid"].startswith("CA_test_")
    assert data["status"] == "queued"
    assert data["to"] == "+15551234567"
    assert data["integrity_event_posted"] is True

    # Twilio recorded the call (kind="call", twiml present)
    twilio = app.state.twilio_client
    call_records = [c for c in twilio.calls if c["kind"] == "call"]
    assert len(call_records) == 1
    rec = call_records[0]
    assert rec["to"] == "+15551234567"
    assert "<Say" in rec["twiml"]
    assert "Your agent has news." in rec["twiml"]

    # Eternitas got the right event
    eternitas = app.state.eternitas_client
    assert len(eternitas.calls) == 1
    event = eternitas.calls[0]
    assert event["passport"] == "ET26-VOIC-AAAA"
    assert event["event_type"] == "voice.call.completed"
    assert event["dimension"] == "reliability"
    assert event["delta_hint"] == 1
    assert event["source"] == "windy-call"
    # Privacy: only country prefix, not full destination
    assert event["context"]["to_country"] == "+15"
    assert "5551234567" not in str(event)


@pytest.mark.asyncio
async def test_voice_uses_explicit_from_when_supplied(auth_client, ept_keypair):
    from app.main import app

    token = sign_test_ept(ept_keypair)
    resp = await auth_client.post(
        "/voice/call",
        headers={"Authorization": f"Bearer {token}"},
        json={"to": "+15551234567", "message": "x", "from": "+15559998888"},
    )
    assert resp.status_code == 200
    rec = [c for c in app.state.twilio_client.calls if c["kind"] == "call"][0]
    assert rec["from_number"] == "+15559998888"


@pytest.mark.asyncio
async def test_voice_503_when_twilio_unconfigured(auth_client, ept_keypair):
    from app.main import app

    app.state.twilio_client._configured = False
    try:
        token = sign_test_ept(ept_keypair)
        resp = await auth_client.post(
            "/voice/call",
            headers={"Authorization": f"Bearer {token}"},
            json={"to": "+15551234567", "message": "x"},
        )
        assert resp.status_code == 503
    finally:
        app.state.twilio_client._configured = True


@pytest.mark.asyncio
async def test_voice_400_on_twilio_4xx(auth_client, ept_keypair):
    """Trial unverified-destination → Twilio 21219; map to 400 with code."""
    from app.main import app

    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json.return_value = {
        "code": 21219,
        "message": "To number is unverified. Trial accounts cannot dial unverified numbers.",
    }
    err = httpx.HTTPStatusError("400", request=MagicMock(), response=mock_resp)
    app.state.twilio_client.raise_exc = err

    try:
        token = sign_test_ept(ept_keypair)
        resp = await auth_client.post(
            "/voice/call",
            headers={"Authorization": f"Bearer {token}"},
            json={"to": "+15551234567", "message": "x"},
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "21219" in detail
    finally:
        app.state.twilio_client.raise_exc = None


@pytest.mark.asyncio
async def test_voice_502_on_twilio_5xx(auth_client, ept_keypair):
    from app.main import app

    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.json.return_value = {"code": 20429, "message": "service unavailable"}
    err = httpx.HTTPStatusError("503", request=MagicMock(), response=mock_resp)
    app.state.twilio_client.raise_exc = err
    try:
        token = sign_test_ept(ept_keypair)
        resp = await auth_client.post(
            "/voice/call",
            headers={"Authorization": f"Bearer {token}"},
            json={"to": "+15551234567", "message": "x"},
        )
        assert resp.status_code == 502
    finally:
        app.state.twilio_client.raise_exc = None


@pytest.mark.asyncio
async def test_voice_502_on_network_error(auth_client, ept_keypair):
    from app.main import app

    app.state.twilio_client.raise_exc = httpx.ConnectError("dns fail")
    try:
        token = sign_test_ept(ept_keypair)
        resp = await auth_client.post(
            "/voice/call",
            headers={"Authorization": f"Bearer {token}"},
            json={"to": "+15551234567", "message": "x"},
        )
        assert resp.status_code == 502
    finally:
        app.state.twilio_client.raise_exc = None
