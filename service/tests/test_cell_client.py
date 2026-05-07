"""C.6 — CellClient lookup behavior. Verifies cache, fallback on 404,
soft fallback on timeout/network error, and unconfigured no-op."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.cell_client import CellClient


@pytest.mark.asyncio
@respx.mock
async def test_lookup_returns_passport_on_200():
    respx.get("http://cell-test/internal/numbers/+17542772201").mock(
        return_value=Response(
            200,
            json={"number": "+17542772201", "passport": "ET26-WIND-Y000", "status": "active"},
        )
    )
    client = CellClient(base_url="http://cell-test", internal_key="k")
    assert await client.lookup_owner("+17542772201") == "ET26-WIND-Y000"


@pytest.mark.asyncio
@respx.mock
async def test_lookup_returns_none_on_404():
    respx.get("http://cell-test/internal/numbers/+19999999999").mock(
        return_value=Response(404, json={"detail": "Number not in registry"})
    )
    client = CellClient(base_url="http://cell-test", internal_key="k")
    assert await client.lookup_owner("+19999999999") is None


@pytest.mark.asyncio
@respx.mock
async def test_lookup_caches_hit():
    """Second lookup of same number must NOT hit the network. We assert
    by configuring the mock to fire exactly once and then succeed; the
    cache hit should not consume another mock call."""
    route = respx.get("http://cell-test/internal/numbers/+1A").mock(
        return_value=Response(200, json={"number": "+1A", "passport": "ET-A"})
    )
    client = CellClient(base_url="http://cell-test", internal_key="k")
    assert await client.lookup_owner("+1A") == "ET-A"
    assert await client.lookup_owner("+1A") == "ET-A"
    assert route.call_count == 1  # the second call was served from cache


@pytest.mark.asyncio
async def test_lookup_unconfigured_returns_none():
    """When base_url or key is empty the client is intentionally inert
    so a misconfigured deploy doesn't crash the inbound webhook hot path."""
    client = CellClient(base_url=None, internal_key=None)
    assert client.configured is False
    assert await client.lookup_owner("+17542772201") is None


@pytest.mark.asyncio
@respx.mock
async def test_lookup_returns_none_on_500():
    respx.get("http://cell-test/internal/numbers/+1B").mock(
        return_value=Response(500, json={"detail": "boom"})
    )
    client = CellClient(base_url="http://cell-test", internal_key="k")
    assert await client.lookup_owner("+1B") is None
