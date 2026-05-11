"""Windy Call FastAPI app — voice-only scaffold (post-T.cleanup).

History: D.1 originally shipped SMS + voice in one repo. Per Grant's
2026-05-07 architectural call, SMS got extracted into the sister
repo `windy-text`. This service is now voice-only — outbound calls,
inbound voice webhooks, voicemail, transcription, spam-report.

Roadmap from here:
  D.2  Per-EII rate limit + cost cap (mirror windy-search B.3+B.9)
  D.3  Outbound voice — POST /voice/call with TwiML response handler
  D.4  Inbound voice webhook (Twilio → /webhooks/twilio/voice) + TwiML
  D.5  Voicemail transcription (Twilio recording webhook + storage)
  D.6  Per-agent number registry (consume future windy-num)
  D.7  Spam-report + auto-suspension
  D.8  Voice-SDK / WebRTC for in-browser calls
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.dependencies import require_passport
from app.auth.ept import PassportClaims
from app.auth.jwks import JWKSCache
from app.cell_client import CellClient
from app.config import get_settings
from app.eii.score_cache import IntegrityScoreCache
from app.eternitas_client import EternitasClient
from app.routes.version import router as version_router
from app.twilio_client import TwilioClient
from app.twilio_inbound.router import router as twilio_inbound_router
from app.voice.router import router as voice_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    redis_client = None
    if settings.redis_url:
        try:
            redis_client = await aioredis.from_url(settings.redis_url)
            await redis_client.ping()
        except Exception:
            redis_client = None
    app.state.redis = redis_client

    app.state.jwks_cache = JWKSCache(jwks_url=settings.eternitas_jwks_url)

    app.state.eternitas_client = EternitasClient(
        base_url=settings.eternitas_base_url,
        platform_api_key=settings.eternitas_platform_api_key,
    )

    app.state.twilio_client = TwilioClient(
        account_sid=settings.twilio_account_sid,
        auth_token=settings.twilio_auth_token,
        from_number=settings.twilio_from_number,
    )

    # D.2.2 — score cache feeds the per-tier cost-cap multiplier so
    # reputation→budget works the same way it does in windy-search.
    app.state.score_cache = IntegrityScoreCache(eternitas_base_url=settings.eternitas_base_url)

    # C.6 — Windy Cell client for resolving inbound `to` → owner passport.
    app.state.cell_client = CellClient(
        base_url=settings.cell_base_url,
        internal_key=settings.cell_internal_key,
    )

    yield

    if redis_client is not None:
        await redis_client.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Windy Call",
        description=(
            "Agent voice telephony for the Windy ecosystem. Every endpoint "
            "is gated by a valid Eternitas passport (EPT JWT). Outbound + "
            "inbound voice calls + voicemail flow through Twilio; integrity "
            "events post to eternitas after each completed action. SMS lives "
            "in the sister service `windy-text` (api.windytext.com)."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/webhooks", status_code=204, include_in_schema=False)
    async def webhooks_inbox() -> None:
        """Eternitas webhook firehose inbox — accept-and-discard stub.

        Eternitas dispatches firehose events to every registered platform.
        Three consecutive failed deliveries auto-deactivate the platform
        key (eternitas/services/webhook_dispatcher.py:272), which would
        silently break our outbound integrity-event posts. Returning 204
        here keeps the consecutive_failures counter at 0.

        Real consumption (HMAC verify, integrity.event handling, etc.)
        lands in a future codon — mirror windy-search/app/webhooks/.
        """
        return None

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "service": settings.service_name,
            "version": "0.1.0",
            "environment": settings.environment,
        }

    @app.get("/health/ready")
    async def health_ready() -> dict:
        """Surface dependency state. Twilio + eternitas are degradable —
        their absence doesn't 503; it's just visible here. Defensive on
        attribute access since ASGITransport tests skip lifespan."""
        twilio = getattr(app.state, "twilio_client", None)
        eternitas = getattr(app.state, "eternitas_client", None)
        cell = getattr(app.state, "cell_client", None)
        redis_ok = True
        if settings.redis_url:
            redis = getattr(app.state, "redis", None)
            try:
                if redis is None:
                    redis_ok = False
                else:
                    await redis.ping()
            except Exception:
                redis_ok = False
        return {
            "status": "ready",
            "redis": redis_ok,
            "twilio_configured": bool(twilio and twilio.configured),
            "eternitas_configured": bool(eternitas and eternitas.configured),
            "cell_configured": bool(cell and cell.configured),
        }

    @app.get("/whoami")
    async def whoami(claims: PassportClaims = Depends(require_passport)) -> dict:
        """EPT self-check. Useful for clients debugging their setup."""
        return {
            "passport": claims.passport,
            "operator_id": claims.operator_id,
            "bot_name": claims.bot_name,
            "bot_type": claims.bot_type,
            "verification_tier": claims.verification_tier,
            "trust_score_legacy": claims.trust_score,
            "expires_at": claims.expires_at,
        }

    app.include_router(voice_router)
    app.include_router(twilio_inbound_router)
    app.include_router(version_router)

    return app


app = create_app()
