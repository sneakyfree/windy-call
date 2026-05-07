"""Windy Cell client — number→passport resolver for inbound webhooks (C.6).

Used by Twilio inbound handlers to answer "which agent owns the number
this message was sent to?" so we can post the integrity event under
the right passport instead of a hardcoded fallback.

Discipline:
  - 1.5s timeout — Twilio's webhook budget is 15s; we can't afford
    cell-api hangs to back up the inbound queue.
  - In-process TTL cache (60s) — number→passport changes rarely
    (port-out is the only real lifecycle event); cache hits avoid the
    HTTP roundtrip on the steady-state path.
  - Soft fallback: 404, timeout, network error all return None. Caller
    decides what to do (drop the message vs use a configured fallback
    passport); we don't make that policy decision here.
  - In-network call: cell-api lives on `deploy_backend` Docker network
    so the URL is `http://cell-api:8800`, not the public TLS endpoint.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60
_LOOKUP_TIMEOUT_SECONDS = 1.5


class CellClient:
    """Stateless HTTP client + tiny in-process owner-passport cache."""

    def __init__(
        self,
        *,
        base_url: str | None,
        internal_key: str | None,
    ) -> None:
        # base_url None / empty → client is "not configured"; .lookup_owner()
        # returns None and the caller will fall back. Same for missing key.
        self.base_url = (base_url or "").rstrip("/")
        self.internal_key = internal_key or ""
        # number → (passport, expires_at_unix)
        self._cache: dict[str, tuple[str, float]] = {}

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.internal_key)

    def _cache_get(self, number: str) -> Optional[str]:
        hit = self._cache.get(number)
        if not hit:
            return None
        passport, expires = hit
        if expires < time.time():
            self._cache.pop(number, None)
            return None
        return passport

    def _cache_put(self, number: str, passport: str) -> None:
        self._cache[number] = (passport, time.time() + _CACHE_TTL_SECONDS)

    async def lookup_owner(self, number: str) -> Optional[str]:
        """Resolve `number` → owner passport. Returns None on miss / err."""
        if not self.configured:
            return None
        cached = self._cache_get(number)
        if cached is not None:
            return cached

        url = f"{self.base_url}/internal/numbers/{number}"
        try:
            async with httpx.AsyncClient(timeout=_LOOKUP_TIMEOUT_SECONDS) as client:
                resp = await client.get(
                    url,
                    headers={"X-Internal-Key": self.internal_key},
                )
        except (httpx.TimeoutException, httpx.HTTPError) as e:
            logger.warning("cell lookup failed for %s: %s", number, e)
            return None

        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            logger.warning(
                "cell lookup unexpected status %d for %s", resp.status_code, number
            )
            return None

        try:
            passport = resp.json().get("passport")
        except ValueError:
            return None
        if not passport:
            return None
        self._cache_put(number, passport)
        return passport
