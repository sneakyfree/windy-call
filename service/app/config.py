"""Pydantic-settings model for the Windy Call service."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Env-driven configuration. Each external dependency optional in D.1
    so the service runs in degraded mode when a piece isn't yet wired."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    service_name: str = "windy-call"
    log_level: str = "INFO"

    host: str = "0.0.0.0"
    port: int = 8600  # 8500 = windy-search; pick a different port

    redis_url: str | None = None

    # --- Eternitas integration ---
    eternitas_base_url: str = "https://api.eternitas.ai"
    eternitas_jwks_url: str = "https://api.eternitas.ai/.well-known/eternitas-keys"
    eternitas_platform_api_key: str | None = None  # et_plt_* — for posting integrity events
    eternitas_webhook_secret: str | None = None    # for HMAC-verifying inbound firehose

    # --- Twilio (D.1: SMS only; voice in later codons) ---
    # WindyFly trial account creds in lockbox; promoted from trial pre-launch.
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    # The platform-owned "from" number used when an agent doesn't have its
    # own number yet. Per-agent number assignment lands in a later codon
    # alongside windy-pro hatch wiring.
    twilio_from_number: str | None = None
    # Public base URL Twilio is configured to POST inbound voice webhooks
    # at. Must match the URL Twilio uses to compute X-Twilio-Signature
    # byte-for-byte (Caddy may rewrite scheme/host on the proxied request,
    # so we trust this configured value over request.url).
    twilio_webhook_base_url: str = "https://api.windycall.com"

    # --- Windy Cell (C.6) — number→passport resolver for inbound routing ---
    # In-network URL on the consolidated EC2 (deploy_backend Docker
    # network); no TLS in path. Caller falls back to fallback_owner_passport
    # if cell isn't reachable / number isn't registered.
    cell_base_url: str = "http://cell-api:8800"
    cell_internal_key: str | None = None
    # Used when cell-api is unreachable OR returns 404. Today this is
    # the founder passport (which owns the only registered number).
    # Once per-agent numbers are real, having an unknown number fall to
    # the founder is the wrong policy — but the right policy is "log +
    # drop" which we'll add in a future codon.
    fallback_owner_passport: str = "ET26-WIND-Y000"

    # --- Voicemail (D.5) ---
    # How many voicemail entries we retain per recipient number. Each
    # entry is metadata only (~300 bytes); audio lives at Twilio for ~30d.
    inbox_max_per_number: int = 100

    # --- Cost cap (D.2) ---
    # Default $5/month per passport. Tier-based caps (Exceptional gets
    # more, Critical less) are a follow-up codon.
    monthly_cost_cap_usd_default: float = 5.0
    monthly_cost_warning_pct: float = 0.80

    cors_origins: list[str] = Field(default_factory=lambda: [
        "https://windycall.com",
        "https://www.windycall.com",
        "https://account.windyword.ai",
        "http://localhost:5173",
    ])


_cached_settings: Settings | None = None


def get_settings() -> Settings:
    global _cached_settings
    if _cached_settings is None:
        _cached_settings = Settings()
    return _cached_settings
