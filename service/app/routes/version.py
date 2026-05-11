"""Deployment-identity endpoint.

GET /version per the MF1 contract in
~/kit-army-config/docs/marathon-foundations-program-2026-05-11.md §MF1.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.config import get_settings

router = APIRouter(tags=["health"])


_STARTED_AT = datetime.now(UTC).isoformat()
_PACKAGE_VERSION = "0.1.0"


class VersionInfo(BaseModel):
    service: str = Field(description="Canonical service name.")
    version: str = Field(description="Semver.")
    commit_sha: str | None
    commit_sha_short: str | None
    build_timestamp: str | None
    started_at: str
    environment: str


@router.get(
    "/version",
    response_model=VersionInfo,
    summary="Deployment identity",
)
async def get_version() -> VersionInfo:
    settings = get_settings()
    commit_sha = os.getenv("COMMIT_SHA") or None
    return VersionInfo(
        service=f"{settings.service_name}-api",
        version=_PACKAGE_VERSION,
        commit_sha=commit_sha,
        commit_sha_short=commit_sha[:7] if commit_sha else None,
        build_timestamp=os.getenv("BUILD_TIMESTAMP") or None,
        started_at=_STARTED_AT,
        environment=settings.environment,
    )
