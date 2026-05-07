"""Twilio webhook signature verification.

Twilio signs each webhook with HMAC-SHA1 over a deterministic
serialization of (URL, sorted form params), base64-encoded. The
recipe is documented at:
  https://www.twilio.com/docs/usage/webhooks/webhooks-security

Anything that doesn't match the exact byte-for-byte concatenation
(case, ordering, URL trailing slash, etc.) yields a different
signature. The canary that you got it wrong is signature mismatch
on every legitimate request.
"""

from __future__ import annotations

import base64
import hashlib
import hmac


def compute_twilio_signature(url: str, params: dict[str, str], auth_token: str) -> str:
    """Build the Twilio HMAC signature for a webhook request.

    Recipe:
      1. Start with the full URL Twilio used (including scheme + host
         + path; query string included only if it was in the original
         POST URL).
      2. Sort the form params alphabetically by key.
      3. Append each `key + value` to the URL string (no separators).
      4. HMAC-SHA1 with the auth_token, base64 encode.
    """
    sorted_keys = sorted(params.keys())
    data = url
    for k in sorted_keys:
        data += k
        data += params[k]
    digest = hmac.new(
        auth_token.encode("utf-8"),
        data.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_twilio_signature(
    url: str,
    params: dict[str, str],
    received_signature: str | None,
    auth_token: str,
) -> bool:
    """Constant-time comparison of expected vs received signatures.

    Returns False on any malformed input (missing header, bad encoding,
    etc.) — never raises.
    """
    if not received_signature or not auth_token:
        return False
    try:
        expected = compute_twilio_signature(url, params, auth_token)
    except Exception:
        return False
    return hmac.compare_digest(expected, received_signature)
