# SUBSTRATE — windy-call production

**ADR:** [ADR-048](https://github.com/sneakyfree/kit-army-config/blob/main/docs/adr-048-operational-substrate-as-code-2026-05-15.md) Layer 1
**Generated:** 2026-05-22 from `service/docker-compose.yml` (dev), `.github/workflows/deploy.yml`. Prod compose hand-curated on EC2 — same gap as windy-cloud/windy-search, see Known Gaps.
**Maintenance policy:** edit on every change to compose, host directory layout, or env vars.
**Confidence flags:** ✓ verified-against-deploy.yml · ⓘ inferred-from-repo · ⚠ known-gap or by-reference.

---

## Host

| Field | Value |
|---|---|
| EC2 instance ID | (in lockbox `ACCESS_LOCKBOX.md`) ⚠ |
| Public IPv4 | (in lockbox + as `DEPLOY_HOST` GHA secret) ⚠ |
| SSH user | `ubuntu` ⓘ |
| Repo path | `/opt/windy-call` ✓ |
| Compose dir | `/opt/windy-call/deploy-prod` ✓ |

Own EC2 (triad services each get their own).

## Compose project

| Field | Value |
|---|---|
| Project name | `windycall-prod` ✓ (per deploy.yml container reference pattern) |
| Dev compose | `service/docker-compose.yml` (`name: windycall-dev`) |
| Prod compose | `/opt/windy-call/deploy-prod/docker-compose.yml` ⚠ **NOT in git** (same gap as cloud/search) |
| Env file | `/opt/windy-call/deploy-prod/.env.production` |

The dev compose mirrors the eternitas/search prod shape.

## Volumes — declared (dev) → on-host (inferred for prod)

| Compose name | On-host name (inferred) | Critical data | Notes |
|---|---|---|---|
| `call-redis-data` | `windycall-prod_call-redis-data` ⓘ | Redis appendonly: voice-provider routing cache, rate-limits | Re-buildable; loss = brief cache miss spike |

No persistent app-data volume — windy-call is stateless voice-provider abstraction per ADR-017 + ADR-044 (Retell + Vapi providers).

## Bind mounts

Unknown for prod. Dev compose declares zero. Inferred for prod: possibly TLS terminator config. To be filled on next live audit.

## Services (expected running in prod)

| Compose service | Container name | Image | Healthy when |
|---|---|---|---|
| call-api | `windycall-prod-call-api-1` ⓘ | `windy-call-api:local` (built in-place from `service/Dockerfile`) | `curl http://localhost:8600/health` + `/version` (MF1) |
| call-redis | `windycall-prod-call-redis-1` ⓘ | `redis:7-alpine` (appendonly, 128M maxmem, allkeys-lru) | `redis-cli ping` |

## External ports (host-bound)

| Port | Service | Purpose |
|---|---|---|
| `127.0.0.1:8600` | call-api → 8600 (container) | API loopback for Caddy proxy to `api.windycall.com` per `[[reference_text_call_split]]` |

## Network

Internal bridge `call-backend` (dev). Prod likely same shape.

## Critical env vars

**Required for boot:**
- `REDIS_URL` (in-network `redis://call-redis:6379/0`)
- `ENVIRONMENT=production`

**Required for voice providers** (per ADR-044 V1.5 voice-provider abstraction):
- `RETELL_API_KEY` (primary)
- `VAPI_API_KEY` (fallback)

**Required for upstream auth:**
- `WINDY_PRO_JWKS_URL` (humans via Pro JWKS RS256)
- `ETERNITAS_JWKS_URL` (agents via Eternitas EPT)

**Required for SMS/voice number coordination** (per Triad master plan):
- `WINDY_CELL_API_URL` (for number lookup)

**Required for Twilio share** (per `[[reference_text_call_split]]` — shared `+17542772201`):
- `TWILIO_*` credentials (in lockbox; primary CP UI-only per `[[feedback_twilio_primary_cp_ui_only]]`)

**MF1 deploy-identity (set by deploy workflow):**
- `COMMIT_SHA`, `BUILD_TIMESTAMP`, `ENVIRONMENT=production`

## Known gaps + audit findings

⚠ **`deploy-prod/docker-compose.yml` is NOT in git** — same gap as windy-cloud + windy-search. Grant-on-return: SSH to the windy-call EC2, capture `docker compose -f deploy-prod/docker-compose.yml config`, sanitize secrets, commit to repo.

⚠ **Live audit pending** — promote ⓘ items to ✓ on next `docker inspect` audit.

## Tolerated drift (allowlist)

| Item | Reason |
|---|---|
| Dev `windycall-dev` vs prod `windycall-prod` project name | Intentional — prod compose lives only on host. |
| `:local` image tag | Sandbox-era pattern. |

## Recovery — cold start

Currently INCOMPLETE without `deploy-prod/docker-compose.yml`. Once that's committed:

1. `git clone https://github.com/sneakyfree/windy-call /opt/windy-call`
2. Restore `deploy-prod/.env.production` from lockbox
3. `cd /opt/windy-call/deploy-prod && sudo docker compose --env-file .env.production up -d`
4. Verify:
   - `curl https://api.windycall.com/health` → `{"status":"healthy"}`
   - `curl https://api.windycall.com/version` → MF1 metadata
   - Voice provider round-trip: place test call via Retell

## Audit history

| Date | Trigger | Result |
|---|---|---|
| 2026-05-22 | Autonomous CTO loop T2.2 backfill (triad batch) | First substrate manifest. Live audit pending. |

## Cross-references

- ADR-017: `kit-army-config/docs/adr-017-windy-triad-architecture.md`
- ADR-044: voice-provider abstraction (Retell + Vapi)
- ADR-048: substrate-as-code
- windy-search SUBSTRATE.md (closest sibling): `/Users/thewindstorm/windy-search/SUBSTRATE.md`
- Memory: `reference_text_call_split.md` (call vs text port/domain split)
- Memory: `feedback_twilio_primary_cp_ui_only.md`
- Memory: `feedback_no_secrets_in_public_docs.md`
- Memory: `reference_lockbox.md`
