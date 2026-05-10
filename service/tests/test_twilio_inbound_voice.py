"""D.4 — Twilio inbound voice webhook tests."""

from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import urlencode

import pytest

AUTH_TOKEN = "32e585f044e0d872a8314b7c51b46f8c"  # WindyFly trial token


def _twilio_sign(url: str, params: dict, auth_token: str = AUTH_TOKEN) -> str:
    sorted_keys = sorted(params.keys())
    data = url + "".join(f"{k}{params[k]}" for k in sorted_keys)
    return base64.b64encode(
        hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    ).decode()


# ---- TwiML builder ------------------------------------------------


def test_voice_twiml_default_greeting():
    from app.twilio_inbound.router import _build_voice_twiml

    twiml = _build_voice_twiml()
    assert twiml.startswith('<?xml')
    assert '<Say voice="alice">' in twiml
    assert '<Hangup/>' in twiml
    # D.5 changed the default greeting to "leave a message after the beep"
    assert 'leave a message' in twiml.lower()


def test_voice_twiml_xml_escapes_caller_supplied_greeting():
    """Even though we control the greeting today, harden the builder so a
    future caller-supplied greeting can't inject TwiML tags."""
    from app.twilio_inbound.router import _build_voice_twiml

    twiml = _build_voice_twiml("hi <Hangup/> & bye")
    assert "<Hangup/> &" not in twiml  # would be the injection
    assert "&lt;Hangup/&gt;" in twiml
    assert "&amp;" in twiml
    # Real <Hangup/> we add ourselves is still present
    assert twiml.count("<Hangup/>") == 1


# ---- /webhooks/twilio/voice endpoint -----------------------------


def _post_voice(client, params: dict, sign: bool = True, auth_token: str = AUTH_TOKEN):
    url = "https://api.windycall.com/webhooks/twilio/voice"
    body = urlencode(params)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if sign:
        headers["X-Twilio-Signature"] = _twilio_sign(url, params, auth_token)
    return client.post(
        "/webhooks/twilio/voice",
        content=body,
        headers=headers,
    )


@pytest.mark.asyncio
async def test_voice_inbound_503_when_unconfigured(auth_client):
    from app.config import get_settings

    settings = get_settings()
    saved = settings.twilio_auth_token
    settings.twilio_auth_token = None
    try:
        resp = await _post_voice(
            auth_client,
            {"CallSid": "CA1", "From": "+1", "To": "+1", "CallStatus": "ringing"},
        )
        assert resp.status_code == 503
    finally:
        settings.twilio_auth_token = saved


@pytest.mark.asyncio
async def test_voice_inbound_403_on_bad_signature(auth_client):
    from app.config import get_settings

    settings = get_settings()
    saved = settings.twilio_auth_token
    settings.twilio_auth_token = AUTH_TOKEN
    try:
        resp = await auth_client.post(
            "/webhooks/twilio/voice",
            content=urlencode(
                {"CallSid": "CA1", "From": "+1", "To": "+1", "CallStatus": "ringing"}
            ),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Twilio-Signature": "deadbeef",
            },
        )
        assert resp.status_code == 403
    finally:
        settings.twilio_auth_token = saved


@pytest.mark.asyncio
async def test_voice_inbound_happy_path_returns_twiml(auth_client):
    from app.config import get_settings

    settings = get_settings()
    saved = settings.twilio_auth_token
    settings.twilio_auth_token = AUTH_TOKEN
    try:
        params = {
            "CallSid": "CA_inbound_test",
            "From": "+18012599358",
            "To": "+17542772201",
            "CallStatus": "ringing",
            "Direction": "inbound",
        }
        resp = await _post_voice(auth_client, params)
        assert resp.status_code == 200
        assert "application/xml" in resp.headers.get("content-type", "")
        body = resp.text
        assert '<Response>' in body
        assert '<Say voice="alice">' in body
        # D.5: the inbound webhook now returns <Record> + a fallback <Hangup>
        assert '<Record ' in body
        assert 'recording-complete' in body  # action callback URL
        assert '<Hangup/>' in body  # fallback after Say if no recording
    finally:
        settings.twilio_auth_token = saved
