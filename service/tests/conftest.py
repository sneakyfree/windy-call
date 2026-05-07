"""Pytest fixtures for windy-call."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.auth_helpers import StubJWKSCache, generate_ept_keypair


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def ept_keypair():
    return generate_ept_keypair()


class RecordingTwilioClient:
    """Drop-in for TwilioClient that captures send_sms + create_call
    calls without touching the wire."""

    def __init__(self, configured: bool = True, raise_exc: Exception | None = None):
        self._configured = configured
        self.raise_exc = raise_exc
        self.calls: list[dict] = []
        self.from_number = "+15555550100"

    @property
    def configured(self) -> bool:
        return self._configured

    async def send_sms(self, *, to, body, from_number=None):
        from app.twilio_client import SMSResult
        self.calls.append({
            "kind": "sms", "to": to, "body": body,
            "from_number": from_number or self.from_number,
        })
        if self.raise_exc:
            raise self.raise_exc
        return SMSResult(
            sid="SM_test_" + str(len(self.calls)),
            status="queued",
            to=to,
            from_=from_number or self.from_number,
            body=body,
            price=None,
            error_code=None,
            error_message=None,
        )

    async def create_call(self, *, to, twiml, from_number=None):
        from app.twilio_client import CallResult
        self.calls.append({
            "kind": "call", "to": to, "twiml": twiml,
            "from_number": from_number or self.from_number,
        })
        if self.raise_exc:
            raise self.raise_exc
        return CallResult(
            sid="CA_test_" + str(len(self.calls)),
            status="queued",
            to=to,
            from_=from_number or self.from_number,
            duration_seconds=None,
            price=None,
            error_code=None,
            error_message=None,
        )


class RecordingEternitasClient:
    def __init__(self, configured: bool = True):
        self.configured = configured
        self.calls: list[dict] = []

    async def submit_integrity_event(self, **kwargs):
        self.calls.append(kwargs)
        if not self.configured:
            return None
        return {"event_id": 1, "delta_actual": 1}


class FakeRedisCostCap:
    """Minimal redis stub for cost_cap.charge primitives (incrby + expire)
    — sufficient for D.2 tests."""

    def __init__(self) -> None:
        self._strings: dict[str, int] = {}

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        pass

    async def incrby(self, key: str, delta: int) -> int:
        self._strings[key] = self._strings.get(key, 0) + int(delta)
        return self._strings[key]

    async def expire(self, key: str, seconds: int) -> bool:
        return True


@pytest_asyncio.fixture
async def auth_client(ept_keypair):
    """Test client with JWKS, Twilio, Eternitas, and cost-cap-Redis stubs."""
    app.state.jwks_cache = StubJWKSCache(ept_keypair["jwks"])
    app.state.twilio_client = RecordingTwilioClient()
    app.state.eternitas_client = RecordingEternitasClient()
    app.state.redis = FakeRedisCostCap()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.state.jwks_cache = None
    app.state.twilio_client = None
    app.state.eternitas_client = None
    app.state.redis = None
