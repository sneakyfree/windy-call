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

    cors_origins: list[str] = Field(default_factory=lambda: [
        "https://windycall.com",
        "https://www.windycall.com",
        "https://app.windyword.ai",
        "http://localhost:5173",
    ])


_cached_settings: Settings | None = None


def get_settings() -> Settings:
    global _cached_settings
    if _cached_settings is None:
        _cached_settings = Settings()
    return _cached_settings
