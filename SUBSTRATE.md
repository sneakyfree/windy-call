# SUBSTRATE — windy-call production

**ADR:** [ADR-048](https://github.com/sneakyfree/kit-army-config/blob/main/docs/adr-048-operational-substrate-as-code-2026-05-15.md) Layer 1
**Generated:** 2026-05-22 from `service/docker-compose.yml` (dev), `.github/workflows/deploy.yml`. Updated 2026-05-26 to reflect the committed `deploy-prod/docker-compose.yml` captured during the prod-compose-capture campaign — see Audit history.
**Maintenance policy:** edit on every change to compose, host directory layout, or env vars.
**Confidence flags:** ✓ verified-against-deploy.yml · ⓘ inferred-from-repo · ⚠ known-gap or by-reference.

---

## Host

| Field | Value |
|---|---|
| EC2 instance ID | `i-07cef803a6a3f86b4` ✓ (consolidated EC2; per `deploy-prod/docker-compose.yml` header) |
| Public IPv4 | `54.88.113.79` ✓ (per same compose header; also tracked in lockbox + `DEPLOY_HOST` GHA secret) |
| SSH user | `ubuntu` ✓ |
| Repo path | `/opt/windy-call` ✓ |
| Compose dir | `/opt/windy-call/deploy-prod` ✓ |

windy-call runs **co-located on the consolidated EC2 `54.88.113.79`** alongside eternitas + windy-mail + windy-pro + windy-search (per `deploy-prod/docker-compose.yml` header).

## Compose project

| Field | Value |
|---|---|
| Project name | `windycall-prod` ✓ (per committed `deploy-prod/docker-compose.yml` `name:` directive) |
| Dev compose | `service/docker-compose.yml` (`name: windycall-dev`) |
| Prod compose | `/opt/windy-call/deploy-prod/docker-compose.yml` ✓ **committed to git** as of 2026-05-26 (prod-compose-capture campaign closed the ADR-048 Layer 1 gap) |
| Env file | `/opt/windy-call/deploy-prod/.env.production` ✓ |

## Volumes — declared (dev) → on-host (inferred for prod)

| Compose name | On-host name | Critical data | Notes |
|---|---|---|---|
| `call-redis-data` | `deploy-prod_call-redis-data` ✓ (external; preserved across the 2026-05-20 project rename per Strategy A) | Redis appendonly: voice-provider routing cache, rate-limits | Re-buildable; loss = brief cache miss spike |

No persistent app-data volume — windy-call is stateless voice-provider abstraction per ADR-017 + ADR-044 (Retell + Vapi providers).

## Bind mounts

Unknown for prod. Dev compose declares zero. Inferred for prod: possibly TLS terminator config. To be filled on next live audit.

## Services (running in prod)

| Compose service | Container name | Image | Healthy when |
|---|---|---|---|
| call-api | `windycall-prod-call-api-1` ✓ | `windy-call-api:local` (built in-place from `service/Dockerfile`) | `curl http://localhost:8600/health` + `/version` (MF1) |
| call-redis | `windycall-prod-call-redis-1` ✓ | `redis:7-alpine` (appendonly, 128M maxmem, allkeys-lru) | `redis-cli ping` |

## External ports (host-bound)

| Port | Service | Purpose |
|---|---|---|
| `127.0.0.1:8610->8600` | call-api (host 8610 → container 8600) ✓ | API loopback for Caddy proxy to `api.windycall.com` per `[[reference_text_call_split]]`. Caddy upstream targets host port 8610 directly. |

## Network

External shared bridge `deploy_backend` ✓ (committed compose declares `networks.backend.external: true, name: deploy_backend`) — co-located with eternitas + windy-mail + windy-pro + windy-search on the same network. Service-name prefix `call-` avoids collisions per compose header.

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

✓ **`deploy-prod/docker-compose.yml` is committed to git** as of 2026-05-26 (prod-compose-capture campaign).

✓ **EC2 ID + IP** are now recorded in the Host section.

## Tolerated drift (allowlist)

| Item | Reason |
|---|---|
| Dev `windycall-dev` vs prod `windycall-prod` project name | Intentional — dev/prod project-name split prevents collisions on the consolidated EC2. |
| `:local` image tag | Sandbox-era pattern. |

## Recovery — cold start

Reproducible from git-state alone (with lockbox-restored `.env.production` and the external `deploy-prod_call-redis-data` volume preserved or rebuilt):

1. `git clone https://github.com/sneakyfree/windy-call /opt/windy-call`
2. Restore `deploy-prod/.env.production` from lockbox
3. Ensure the external `deploy-prod_call-redis-data` docker volume exists (cache repopulates on first lookup if absent)
4. `cd /opt/windy-call/deploy-prod && sudo docker compose --env-file .env.production up -d`
5. Verify:
   - `curl https://api.windycall.com/health` → `{"status":"healthy"}`
   - `curl https://api.windycall.com/version` → MF1 metadata
   - Voice provider round-trip: place test call via Retell

## Audit history

| Date | Trigger | Result |
|---|---|---|
| 2026-05-22 | Autonomous CTO loop T2.2 backfill (triad batch) | First substrate manifest. Live audit pending. |
| 2026-05-26 | Prod-compose-capture campaign (5 parallel SSH-verified captures) | `deploy-prod/docker-compose.yml` committed to git. **Corrected port binding** (`127.0.0.1:8610->8600`, NOT `:8600`) — Caddy upstream targets host port 8610 directly; container internal port stays 8600. Corrected host EC2 (consolidated `54.88.113.79`, not own EC2). Corrected network (shared `deploy_backend`, not isolated `call-backend`). Promoted ⓘ→✓ on project name, container names, volume on-host name, external network. |

## Cross-references

- ADR-017: `kit-army-config/docs/adr-017-windy-triad-architecture.md`
- ADR-044: voice-provider abstraction (Retell + Vapi)
- ADR-048: substrate-as-code
- windy-search SUBSTRATE.md (closest sibling): `/Users/thewindstorm/windy-search/SUBSTRATE.md`
- Memory: `reference_text_call_split.md` (call vs text port/domain split)
- Memory: `feedback_twilio_primary_cp_ui_only.md`
- Memory: `feedback_no_secrets_in_public_docs.md`
- Memory: `reference_lockbox.md`
