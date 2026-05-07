"""D.2 — cost cap tests for /voice/call."""

from __future__ import annotations

import pytest

from tests.auth_helpers import sign_test_ept


def test_voice_cost_in_catalog():
    from app.eii.cost_cap import COSTS

    # voice.call = 50_000 microcents = $0.05/call (conservative ~4-min)
    assert COSTS["voice.call"] == 50_000


@pytest.mark.asyncio
async def test_voice_call_response_carries_cost_headers(auth_client, ept_keypair):
    token = sign_test_ept(ept_keypair, passport="ET26-VOIC-COST")
    resp = await auth_client.post(
        "/voice/call",
        headers={"Authorization": f"Bearer {token}"},
        json={"to": "+15551234567", "message": "x"},
    )
    assert resp.status_code == 200
    assert resp.headers["X-Cost-Capability"] == "voice.call"
    assert resp.headers["X-Cost-Cap-USD"] == "5.00"
    # voice.call costs 50_000 microcents → $0.050000
    assert resp.headers["X-Cost-Used-USD"].startswith("0.050000")


@pytest.mark.asyncio
async def test_voice_call_429_when_budget_exhausted(auth_client, ept_keypair):
    """Pre-charge the counter past the cap, then verify a fresh call gets 429."""
    from app.eii.cost_cap import _key, MICROCENTS_PER_USD
    from app.main import app

    passport = "ET26-COST-EXHA"
    redis = app.state.redis
    redis._strings[_key(passport)] = 5 * MICROCENTS_PER_USD  # at the $5 cap

    token = sign_test_ept(ept_keypair, passport=passport)
    resp = await auth_client.post(
        "/voice/call",
        headers={"Authorization": f"Bearer {token}"},
        json={"to": "+15551234567", "message": "x"},
    )
    assert resp.status_code == 429
    assert "budget" in resp.json()["detail"].lower()
    assert resp.headers["Retry-After"] == "86400"
    assert resp.headers["X-Cost-Capability"] == "voice.call"


@pytest.mark.asyncio
async def test_voice_call_warning_at_threshold(auth_client, ept_keypair):
    """Crossing the 80% warning threshold sets X-Cost-Warning."""
    from app.eii.cost_cap import _key, MICROCENTS_PER_USD
    from app.main import app

    passport = "ET26-WARN-AAAA"
    redis = app.state.redis
    # 80% of $5 = $4.00 = 4_000_000 microcents. Pre-charge to 3_960_000
    # so this $0.05 call pushes us across the threshold.
    redis._strings[_key(passport)] = 3_960_000

    token = sign_test_ept(ept_keypair, passport=passport)
    resp = await auth_client.post(
        "/voice/call",
        headers={"Authorization": f"Bearer {token}"},
        json={"to": "+15551234567", "message": "x"},
    )
    assert resp.status_code == 200
    assert "X-Cost-Warning" in resp.headers
    assert "80%" in resp.headers["X-Cost-Warning"]


@pytest.mark.asyncio
async def test_voice_call_cost_isolated_per_passport(auth_client, ept_keypair):
    """One passport's exhaustion doesn't lock out a different passport."""
    from app.eii.cost_cap import _key, MICROCENTS_PER_USD
    from app.main import app

    redis = app.state.redis
    redis._strings[_key("ET26-AAAA-AAAA")] = 5 * MICROCENTS_PER_USD  # exhausted

    # Different passport — fresh budget
    token = sign_test_ept(ept_keypair, passport="ET26-BBBB-BBBB")
    resp = await auth_client.post(
        "/voice/call",
        headers={"Authorization": f"Bearer {token}"},
        json={"to": "+15551234567", "message": "x"},
    )
    assert resp.status_code == 200
    assert resp.headers["X-Cost-Used-USD"].startswith("0.050000")


# -------------------------------------------------------------------------
# D.2.2 — per-tier cost-cap multiplier tests (parallels windy-search B.9.2)
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_exceptional_tier_50_dollar_cap(auth_client, ept_keypair):
    from app.main import app

    app.state.score_cache.scores["ET26-VOIC-EXC"] = 950
    token = sign_test_ept(ept_keypair, passport="ET26-VOIC-EXC")
    resp = await auth_client.post(
        "/voice/call",
        headers={"Authorization": f"Bearer {token}"},
        json={"to": "+15551234567", "message": "x"},
    )
    assert resp.status_code == 200
    assert resp.headers["X-Cost-Tier"] == "exceptional"
    assert resp.headers["X-Cost-Tier-Multiplier"] == "10"
    assert resp.headers["X-Cost-Cap-USD"] == "50.00"


@pytest.mark.asyncio
async def test_voice_critical_tier_50_cent_cap(auth_client, ept_keypair):
    from app.main import app

    app.state.score_cache.scores["ET26-VOIC-CRIT"] = 100
    token = sign_test_ept(ept_keypair, passport="ET26-VOIC-CRIT")
    resp = await auth_client.post(
        "/voice/call",
        headers={"Authorization": f"Bearer {token}"},
        json={"to": "+15551234567", "message": "x"},
    )
    assert resp.status_code == 200
    assert resp.headers["X-Cost-Tier"] == "critical"
    assert resp.headers["X-Cost-Cap-USD"] == "0.50"


@pytest.mark.asyncio
async def test_voice_critical_tier_429_at_50_cents(auth_client, ept_keypair):
    """Critical tier ($0.50 cap) blocks the next call once 50 cents is spent."""
    from app.eii.cost_cap import _key
    from app.main import app

    app.state.score_cache.scores["ET26-VOIC-EXH"] = 100
    redis = app.state.redis
    redis._strings[_key("ET26-VOIC-EXH")] = 500_000  # $0.50

    token = sign_test_ept(ept_keypair, passport="ET26-VOIC-EXH")
    resp = await auth_client.post(
        "/voice/call",
        headers={"Authorization": f"Bearer {token}"},
        json={"to": "+15551234567", "message": "x"},
    )
    assert resp.status_code == 429
    assert "critical" in resp.json()["detail"].lower()
