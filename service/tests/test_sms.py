"""D.1 — /sms/send endpoint tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from tests.auth_helpers import sign_test_ept


# ---- E.164 + body validation ----------------------------------------


@pytest.mark.asyncio
async def test_sms_requires_authorization(auth_client):
    resp = await auth_client.post("/sms/send", json={"to": "+15555551212", "body": "hi"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_sms_rejects_non_e164(auth_client, ept_keypair):
    token = sign_test_ept(ept_keypair)
    resp = await auth_client.post(
        "/sms/send",
        headers={"Authorization": f"Bearer {token}"},
        json={"to": "5555551212", "body": "hi"},  # missing leading +
    )
    assert resp.status_code == 400
    assert "E.164" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_sms_rejects_empty_body(auth_client, ept_keypair):
    token = sign_test_ept(ept_keypair)
    resp = await auth_client.post(
        "/sms/send",
        headers={"Authorization": f"Bearer {token}"},
        json={"to": "+15555551212", "body": ""},
    )
    assert resp.status_code == 422  # Pydantic min_length


@pytest.mark.asyncio
async def test_sms_rejects_oversize_body(auth_client, ept_keypair):
    token = sign_test_ept(ept_keypair)
    resp = await auth_client.post(
        "/sms/send",
        headers={"Authorization": f"Bearer {token}"},
        json={"to": "+15555551212", "body": "x" * 1000},
    )
    assert resp.status_code == 422  # Pydantic max_length


# ---- happy path -----------------------------------------------------


@pytest.mark.asyncio
async def test_sms_happy_path_posts_event(auth_client, ept_keypair):
    from app.main import app

    token = sign_test_ept(ept_keypair, passport="ET26-CALL-AAAA")
    resp = await auth_client.post(
        "/sms/send",
        headers={"Authorization": f"Bearer {token}"},
        json={"to": "+15551234567", "body": "Hello from your agent"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["sid"].startswith("SM_test_")
    assert data["status"] == "queued"
    assert data["to"] == "+15551234567"
    assert data["from"] == "+15555550100"  # platform default in stub
    assert data["integrity_event_posted"] is True

    # Twilio recorded the call
    twilio = app.state.twilio_client
    assert len(twilio.calls) == 1
    call = twilio.calls[0]
    assert call["to"] == "+15551234567"
    assert call["body"] == "Hello from your agent"

    # Eternitas got the event
    eternitas = app.state.eternitas_client
    assert len(eternitas.calls) == 1
    event = eternitas.calls[0]
    assert event["passport"] == "ET26-CALL-AAAA"
    assert event["event_type"] == "sms.send.completed"
    assert event["dimension"] == "reliability"
    assert event["delta_hint"] == 1
    assert event["source"] == "windy-call"
    # Privacy: only country prefix, not full destination
    assert event["context"]["to_country"] == "+15"
    assert "5551234567" not in str(event)


@pytest.mark.asyncio
async def test_sms_uses_explicit_from_when_supplied(auth_client, ept_keypair):
    from app.main import app

    token = sign_test_ept(ept_keypair)
    resp = await auth_client.post(
        "/sms/send",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "to": "+15551234567",
            "body": "x",
            "from": "+15559998888",
        },
    )
    assert resp.status_code == 200
    assert app.state.twilio_client.calls[0]["from_number"] == "+15559998888"


# ---- error mapping --------------------------------------------------


@pytest.mark.asyncio
async def test_sms_503_when_twilio_unconfigured(auth_client, ept_keypair):
    from app.main import app

    app.state.twilio_client._configured = False
    try:
        token = sign_test_ept(ept_keypair)
        resp = await auth_client.post(
            "/sms/send",
            headers={"Authorization": f"Bearer {token}"},
            json={"to": "+15551234567", "body": "x"},
        )
        assert resp.status_code == 503
    finally:
        app.state.twilio_client._configured = True


@pytest.mark.asyncio
async def test_sms_400_on_twilio_4xx(auth_client, ept_keypair):
    """Twilio 21608 (unverified destination, trial constraint) maps to 400."""
    from app.main import app

    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json.return_value = {
        "code": 21608,
        "message": "The number is unverified. Trial accounts cannot send messages to unverified numbers.",
    }
    err = httpx.HTTPStatusError("400", request=MagicMock(), response=mock_resp)
    app.state.twilio_client.raise_exc = err

    try:
        token = sign_test_ept(ept_keypair)
        resp = await auth_client.post(
            "/sms/send",
            headers={"Authorization": f"Bearer {token}"},
            json={"to": "+15551234567", "body": "x"},
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "21608" in detail
        assert "unverified" in detail.lower()
    finally:
        app.state.twilio_client.raise_exc = None


@pytest.mark.asyncio
async def test_sms_502_on_twilio_5xx(auth_client, ept_keypair):
    from app.main import app

    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.json.return_value = {"code": 20429, "message": "service unavailable"}
    err = httpx.HTTPStatusError("503", request=MagicMock(), response=mock_resp)
    app.state.twilio_client.raise_exc = err

    try:
        token = sign_test_ept(ept_keypair)
        resp = await auth_client.post(
            "/sms/send",
            headers={"Authorization": f"Bearer {token}"},
            json={"to": "+15551234567", "body": "x"},
        )
        assert resp.status_code == 502
    finally:
        app.state.twilio_client.raise_exc = None


@pytest.mark.asyncio
async def test_sms_502_on_network_error(auth_client, ept_keypair):
    from app.main import app

    app.state.twilio_client.raise_exc = httpx.ConnectError("dns fail")
    try:
        token = sign_test_ept(ept_keypair)
        resp = await auth_client.post(
            "/sms/send",
            headers={"Authorization": f"Bearer {token}"},
            json={"to": "+15551234567", "body": "x"},
        )
        assert resp.status_code == 502
    finally:
        app.state.twilio_client.raise_exc = None
