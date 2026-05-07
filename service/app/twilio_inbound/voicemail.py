"""Voicemail storage primitives (Phase D.5).

One Redis LIST per recipient number, capped at INBOX_MAX_PER_NUMBER.
Newest message at index 0 (LPUSH); LTRIM keeps the list bounded.
Each entry is JSON-serialized: {recording_sid, recording_url,
duration_seconds, from, to, recorded_at, transcription_text?}.

The audio file itself lives at Twilio (their RecordingUrl, retained
~30 days by default). We just store the URL + metadata. A future
codon migrates audio to Windy Cloud / R2 for durable retention.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

KEY_PREFIX = "voicemail:windy-call:to"


def _key(to_number: str) -> str:
    return f"{KEY_PREFIX}:{to_number}"


async def store_voicemail(
    redis: Optional[aioredis.Redis],
    *,
    to: str,
    payload: dict[str, Any],
    max_size: int,
) -> bool:
    """LPUSH a new voicemail metadata blob onto the head of the inbox."""
    if redis is None:
        logger.warning("voicemail redis unavailable — message metadata not stored")
        return False

    key = _key(to)
    blob = json.dumps({"_stored_at": int(time.time()), **payload})
    try:
        await redis.lpush(key, blob)
        await redis.ltrim(key, 0, max_size - 1)
        await redis.expire(key, 30 * 86400)
        return True
    except Exception as e:
        logger.warning("voicemail lpush failed for %s: %s", to, e)
        return False


async def fetch_voicemails(
    redis: Optional[aioredis.Redis],
    *,
    to: str,
    limit: int = 25,
) -> list[dict[str, Any]]:
    if redis is None:
        return []
    try:
        raw = await redis.lrange(_key(to), 0, limit - 1)
    except Exception as e:
        logger.warning("voicemail lrange failed for %s: %s", to, e)
        return []

    out: list[dict[str, Any]] = []
    for item in raw:
        try:
            out.append(json.loads(item if isinstance(item, str) else item.decode()))
        except (ValueError, UnicodeDecodeError):
            continue
    return out
