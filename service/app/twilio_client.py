"""Async HTTP client for the Twilio API.

We don't pull in twilio-python — the dependency surface is large and
we only need a handful of endpoints. Direct httpx with HTTP Basic
auth keeps the surface small and makes test stubbing trivial.

Twilio API docs: https://www.twilio.com/docs/usage/api
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.twilio.com/2010-04-01"


@dataclass(frozen=True)
class SMSResult:
    """Outcome of a Twilio /Messages POST."""
    sid: str
    status: str           # queued | sending | sent | delivered | failed | …
    to: str
    from_: str
    body: str
    price: str | None
    error_code: int | None
    error_message: str | None


class TwilioClient:
    """Minimal Twilio REST wrapper.

    Failure semantics: Twilio errors are RAISED (not swallowed) — the
    route handler decides whether to surface as 4xx (caller's fault,
    e.g. bad number) or 5xx (Twilio outage). This is the inverse of
    the EternitasClient which logs-and-swallows because integrity
    events are best-effort.
    """

    def __init__(
        self,
        account_sid: Optional[str],
        auth_token: Optional[str],
        from_number: Optional[str] = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self.timeout = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.account_sid and self.auth_token)

    def _auth(self) -> tuple[str, str]:
        return (self.account_sid or "", self.auth_token or "")

    async def send_sms(
        self,
        *,
        to: str,
        body: str,
        from_number: Optional[str] = None,
    ) -> SMSResult:
        """POST /Accounts/{sid}/Messages.json — outbound SMS.

        `from_number` overrides the platform-default. Required to be a
        Twilio number you own OR an alphanumeric sender (region-dependent).

        Raises:
          RuntimeError if the client isn't configured.
          httpx.HTTPStatusError on non-2xx — message body has Twilio's
            error code + message; the route handler maps those.
        """
        if not self.configured:
            raise RuntimeError("Twilio client not configured")

        url = f"{API_BASE}/Accounts/{self.account_sid}/Messages.json"
        payload = {
            "To": to,
            "Body": body,
            "From": from_number or self.from_number,
        }
        if not payload["From"]:
            raise RuntimeError("No From number configured (TWILIO_FROM_NUMBER)")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, auth=self._auth(), data=payload)

        # Surface Twilio's specific error code/message via the response
        # body — caller routes by status. raise_for_status() includes the
        # body in the exception's response attribute.
        resp.raise_for_status()
        data = resp.json()
        return SMSResult(
            sid=data.get("sid", ""),
            status=data.get("status", "unknown"),
            to=data.get("to", to),
            from_=data.get("from", payload["From"]),
            body=data.get("body", body),
            price=data.get("price"),
            error_code=data.get("error_code"),
            error_message=data.get("error_message"),
        )

    async def get_balance(self) -> dict:
        """Trial balance + currency. Useful for the /health/ready probe."""
        if not self.configured:
            raise RuntimeError("Twilio client not configured")
        url = f"{API_BASE}/Accounts/{self.account_sid}/Balance.json"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, auth=self._auth())
        resp.raise_for_status()
        return resp.json()
