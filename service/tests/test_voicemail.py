"""D.5 — voicemail recording-complete webhook + agent inbox tests."""

from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import urlencode

import pytest

from tests.auth_helpers import sign_test_ept


AUTH_TOKEN = "32e585f044e0d872a8314b7c51b46f8c"


def _twilio_sign(url: str, params: dict, auth_token: str = AUTH_TOKEN) -> str:
    sorted_keys = sorted(params.keys())
    data = url + "".join(f"{k}{params[k]}" for k in sorted_keys)
    return base64.b64encode(
        hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    ).decode()


# ---- voicemail storage primitives ---------------------------------


@pytest.mark.asyncio
async def test_voicemail_store_and_fetch_round_trip():
    from app.twilio_inbound.voicemail import fetch_voicemails, store_voicemail
    from tests.conftest import FakeRedisCostCap

    redis = FakeRedisCostCap()
    await store_voicemail(
        redis,
        to="+17542772201",
        payload={"recording_sid": "RE1", "from": "+1A", "to": "+17542772201",
                 "recording_url": "https://api.twilio.com/.../RE1", "duration_seconds": 12,
                 "call_sid": "CA1"},
        max_size=100,
    )
    msgs = await fetch_voicemails(redis, to="+17542772201", limit=10)
    assert len(msgs) == 1
    assert msgs[0]["recording_sid"] == "RE1"
    assert msgs[0]["duration_seconds"] == 12
    assert "_stored_at" in msgs[0]


@pytest.mark.asyncio
async def test_voicemail_lpush_newest_first_and_ltrim():
    from app.twilio_inbound.voicemail import fetch_voicemails, store_voicemail
    from tests.conftest import FakeRedisCostCap

    redis = FakeRedisCostCap()
    for i in range(15):
        await store_voicemail(
            redis,
            to="+17542772201",
            payload={"recording_sid": f"RE{i}", "from": "+1", "to": "+17542772201",
                     "recording_url": "u", "duration_seconds": 1, "call_sid": "c"},
            max_size=10,
        )
    msgs = await fetch_voicemails(redis, to="+17542772201", limit=100)
    assert len(msgs) == 10
    assert msgs[0]["recording_sid"] == "RE14"   # newest first
    assert msgs[-1]["recording_sid"] == "RE5"   # oldest retained


# ---- /webhooks/twilio/voice/recording-complete --------------------


def _post_recording_complete(client, params: dict, sign: bool = True):
    url = "https://api.windycall.com/webhooks/twilio/voice/recording-complete"
    body = urlencode(params)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if sign:
        headers["X-Twilio-Signature"] = _twilio_sign(url, params)
    return client.post(
        "/webhooks/twilio/voice/recording-complete",
        content=body,
        headers=headers,
    )


@pytest.mark.asyncio
async def test_recording_complete_403_on_bad_signature(auth_client):
    from app.config import get_settings

    settings = get_settings()
    saved = settings.twilio_auth_token
    settings.twilio_auth_token = AUTH_TOKEN
    try:
        resp = await auth_client.post(
            "/webhooks/twilio/voice/recording-complete",
            content=urlencode({"RecordingSid": "RE1"}),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Twilio-Signature": "deadbeef",
            },
        )
        assert resp.status_code == 403
    finally:
        settings.twilio_auth_token = saved


@pytest.mark.asyncio
async def test_recording_complete_stores_and_returns_thank_you(auth_client):
    from app.config import get_settings
    from app.main import app

    settings = get_settings()
    saved = settings.twilio_auth_token
    settings.twilio_auth_token = AUTH_TOKEN
    try:
        params = {
            "RecordingSid": "RE_test_voicemail",
            "RecordingUrl": "https://api.twilio.com/Accounts/AC.../Recordings/RE_test_voicemail",
            "RecordingDuration": "23",
            "RecordingStatus": "completed",
            "From": "+18012599358",
            "To": "+17542772201",
            "CallSid": "CA_voicemail_test",
        }
        resp = await _post_recording_complete(auth_client, params)
        assert resp.status_code == 200
        body = resp.text
        assert "<Response>" in body
        assert "<Say" in body
        assert "Thank you" in body or "thank you" in body
        assert "<Hangup/>" in body

        # Voicemail landed in the inbox
        from app.twilio_inbound.voicemail import fetch_voicemails
        msgs = await fetch_voicemails(app.state.redis, to="+17542772201", limit=5)
        assert len(msgs) == 1
        assert msgs[0]["recording_sid"] == "RE_test_voicemail"
        assert msgs[0]["duration_seconds"] == 23
        assert msgs[0]["from"] == "+18012599358"
    finally:
        settings.twilio_auth_token = saved


# ---- /voice/voicemail (agent-facing) ----------------------------


@pytest.mark.asyncio
async def test_voicemail_inbox_requires_auth(auth_client):
    resp = await auth_client.get("/voice/voicemail?to=%2B17542772201")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_voicemail_inbox_returns_messages(auth_client, ept_keypair):
    from app.config import get_settings

    settings = get_settings()
    saved = settings.twilio_auth_token
    settings.twilio_auth_token = AUTH_TOKEN
    try:
        for sid in ("RE_a", "RE_b"):
            await _post_recording_complete(
                auth_client,
                {"RecordingSid": sid, "RecordingUrl": "u", "RecordingDuration": "5",
                 "RecordingStatus": "completed", "From": "+1A", "To": "+17542772201",
                 "CallSid": "C1"},
            )

        token = sign_test_ept(ept_keypair)
        resp = await auth_client.get(
            "/voice/voicemail?to=%2B17542772201&limit=5",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["to"] == "+17542772201"
        assert data["count"] == 2
        # newest first
        sids = [m["recording_sid"] for m in data["voicemails"]]
        assert sids == ["RE_b", "RE_a"]
    finally:
        settings.twilio_auth_token = saved
