"""FastAPI dependencies for EPT-gated routes.

D.1 introduced require_passport (EPT verification only).
D.2 adds require_passport_with_cost_cap — composes auth + per-passport
monthly USD spend ceiling. Pure rate-limiting (independent of cost) is
deferred; voice + SMS are inherently slow so cost is the right defense.
"""

from fastapi import Depends, Header, HTTPException, Request, Response

from app.auth.ept import PassportClaims, verify_ept
from app.config import get_settings
from app.eii import cost_cap


async def require_passport(
    request: Request,
    authorization: str | None = Header(default=None),
) -> PassportClaims:
    """Verify the Authorization Bearer EPT and return claims.

    Routes that need a valid passport take this as a dependency:

        @router.get("/whoami")
        async def whoami(claims: PassportClaims = Depends(require_passport)):
            return claims
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer EPT required in Authorization header")

    jwks_cache = getattr(request.app.state, "jwks_cache", None)
    if jwks_cache is None:
        # B.2 wires this in lifespan. Returning 503 (rather than 401) makes
        # mis-configured deployments observable as service issues, not
        # client auth failures.
        raise HTTPException(status_code=503, detail="EPT verification not configured")

    token = authorization[len("Bearer "):]
    return await verify_ept(token, jwks_cache)


def require_passport_with_cost_cap(capability: str):
    """D.2 — factory composing EPT verify + monthly USD cost cap.

    Routes use it like:

        @router.post("/voice/call", ...)
        async def create_voice_call(
            ...,
            claims: PassportClaims = Depends(require_passport_with_cost_cap("voice.call")),
        ): ...

    Cost catalog lives in app/eii/cost_cap.py:COSTS. Exceeding the
    monthly cap returns 429 with X-Cost-* headers + Retry-After=86400.
    Successful calls return 200 with the cost-state surfaced via:

        X-Cost-Cap-USD       — the ceiling
        X-Cost-Used-USD      — accumulated this month after this call
        X-Cost-Capability    — which capability charged
        X-Cost-Warning       — only present after crossing 80% threshold
    """
    async def _dep(
        request: Request,
        response: Response,
        claims: PassportClaims = Depends(require_passport),
    ) -> PassportClaims:
        settings = get_settings()
        redis = getattr(request.app.state, "redis", None)
        decision = await cost_cap.charge(
            redis,
            passport=claims.passport,
            capability=capability,
            cap_usd=settings.monthly_cost_cap_usd_default,
            warning_pct=settings.monthly_cost_warning_pct,
        )

        response.headers["X-Cost-Cap-USD"] = f"{decision.cap_microcents / cost_cap.MICROCENTS_PER_USD:.2f}"
        response.headers["X-Cost-Used-USD"] = f"{decision.used_after / cost_cap.MICROCENTS_PER_USD:.6f}"
        response.headers["X-Cost-Capability"] = capability
        if decision.warning:
            response.headers["X-Cost-Warning"] = (
                f"Crossed {int(settings.monthly_cost_warning_pct * 100)}% of monthly budget"
            )

        if not decision.allowed:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Monthly budget exhausted (cap "
                    f"${decision.cap_microcents / cost_cap.MICROCENTS_PER_USD:.2f}). "
                    f"Resets on the 1st."
                ),
                headers={
                    "Retry-After": "86400",
                    "X-Cost-Cap-USD": f"{decision.cap_microcents / cost_cap.MICROCENTS_PER_USD:.2f}",
                    "X-Cost-Used-USD": f"{decision.used_after / cost_cap.MICROCENTS_PER_USD:.6f}",
                    "X-Cost-Capability": capability,
                },
            )

        return claims

    return _dep
