"""Windy Call FastAPI app — Phase D.1 scaffold + first SMS endpoint.

This codon establishes the service skeleton + outbound SMS path.
Subsequent codons add:
  D.2  — Per-EII rate limiter + cost cap (mirror windy-search B.3+B.9)
  D.3  — Outbound voice (POST /voice/call)
  D.4  — Inbound SMS webhook (Twilio → /webhooks/twilio/sms)
  D.5  — Inbound voice webhook + TwiML
  D.6  — Number provisioning per agent (windy-pro hatch wiring)
  D.7  — Voicemail transcription
  D.8  — Spam-report + auto-suspension
  D.9  — Deploy + DNS (api.windycall.com)
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.dependencies import require_passport
from app.auth.ept import PassportClaims
from app.auth.jwks import JWKSCache
from app.config import get_settings
from app.eternitas_client import EternitasClient
from app.sms.router import router as sms_router
from app.twilio_client import TwilioClient


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

    yield

    if redis_client is not None:
        await redis_client.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Windy Call",
        description=(
            "Agent telephony for the Windy ecosystem. Every endpoint is "
            "gated by a valid Eternitas passport (EPT JWT). Outbound SMS "
            "+ voice calls flow through Twilio; integrity events post to "
            "eternitas after each completed action."
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

    app.include_router(sms_router)

    return app


app = create_app()
