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
from app.eii.tiers import tier_for_score


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
    """D.2 + D.2.2 — factory composing EPT verify + tier-scaled monthly
    USD cost cap. Mirrors windy-search/B.9.2.

    Cap = settings.monthly_cost_cap_usd_default × tier.cost_cap_multiplier
    where tier comes from the same EII score that drives windy-search's
    rate-limit tier (B.3). Same reputation lever, different effect:

      Exceptional (900+) → ×10 → $50/month
      Trusted     (700+) → ×5  → $25/month
      Developing  (500+) → ×1  → $5/month  ← baseline
      Watch       (400+) → ×0.4→ $2/month
      Critical    (<400) → ×0.1→ $0.50/month
    """
    async def _dep(
        request: Request,
        response: Response,
        claims: PassportClaims = Depends(require_passport),
    ) -> PassportClaims:
        settings = get_settings()
        redis = getattr(request.app.state, "redis", None)
        score_cache = getattr(request.app.state, "score_cache", None)

        # D.2.2 — scale base cap by passport's tier multiplier.
        # Default to 1.0× when score_cache is unconfigured (matches the
        # cost_cap fail-open posture).
        cap_multiplier = 1.0
        tier_name = "developing"
        if score_cache is not None:
            score = await score_cache.get(claims.passport)
            tier = tier_for_score(score)
            cap_multiplier = tier.cost_cap_multiplier
            tier_name = tier.name

        cap_usd = settings.monthly_cost_cap_usd_default * cap_multiplier
        decision = await cost_cap.charge(
            redis,
            passport=claims.passport,
            capability=capability,
            cap_usd=cap_usd,
            warning_pct=settings.monthly_cost_warning_pct,
        )

        response.headers["X-Cost-Cap-USD"] = f"{decision.cap_microcents / cost_cap.MICROCENTS_PER_USD:.2f}"
        response.headers["X-Cost-Used-USD"] = f"{decision.used_after / cost_cap.MICROCENTS_PER_USD:.6f}"
        response.headers["X-Cost-Capability"] = capability
        response.headers["X-Cost-Tier"] = tier_name
        response.headers["X-Cost-Tier-Multiplier"] = f"{cap_multiplier:g}"
        if decision.warning:
            response.headers["X-Cost-Warning"] = (
                f"Crossed {int(settings.monthly_cost_warning_pct * 100)}% of monthly budget"
            )

        if not decision.allowed:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Monthly budget exhausted ({tier_name} tier cap "
                    f"${decision.cap_microcents / cost_cap.MICROCENTS_PER_USD:.2f}). "
                    f"Resets on the 1st."
                ),
                headers={
                    "Retry-After": "86400",
                    "X-Cost-Cap-USD": f"{decision.cap_microcents / cost_cap.MICROCENTS_PER_USD:.2f}",
                    "X-Cost-Used-USD": f"{decision.used_after / cost_cap.MICROCENTS_PER_USD:.6f}",
                    "X-Cost-Capability": capability,
                    "X-Cost-Tier": tier_name,
                },
            )

        return claims

    return _dep
