"""D.1 — health + readiness."""

import pytest


@pytest.mark.asyncio
async def test_health_returns_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "windy-call"


@pytest.mark.asyncio
async def test_health_ready_surfaces_dep_state(client):
    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert "twilio_configured" in data
    assert "eternitas_configured" in data


@pytest.mark.asyncio
async def test_openapi_lists_sms_endpoint(client):
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.json()
    assert spec["info"]["title"] == "Windy Call"
    assert "/sms/send" in spec["paths"]
    assert "/whoami" in spec["paths"]
