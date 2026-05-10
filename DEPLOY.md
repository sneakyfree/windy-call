# Windy Call — Production Deployment Guide

EC2 deployment pattern for `api.windycall.com`. M0/M1 era: single-node FastAPI service + Redis behind Caddy. Voice-provider abstraction (M2) and recording-storage S3 (M3) extend the deploy surface in §12–§14.

> **Companion to:** [Windy Triad master plan §6 M0.9](https://github.com/sneakyfree/kit-army-config/blob/main/docs/windy-triad-master-plan-2026-05-10.md), [ADR-017](https://github.com/sneakyfree/kit-army-config/blob/main/docs/adr-017-windy-triad-architecture.md).

---

## 1. Target infrastructure

| Component | Target |
|---|---|
| Compute | AWS EC2 (single node M0/M1; ALB+ASG once V1 ships) |
| Reverse proxy / TLS | Caddy 2 (auto-HTTPS via Let's Encrypt) |
| App container | `service/Dockerfile` runtime stage on port `:8600` |
| Cache / cost-cap state | Redis 7 (containerized) |
| Voice + SMS carrier (V1) | Twilio direct (M2 wraps under provider abstraction) |
| Number registry | `api.windycell.com` (Cell service) — consumed via cell-client |
| LLM brain (M3+) | `api.windymind.ai/v1/chat` per master plan §4 P6 |
| Recording storage (M3+) | S3 `windy-call-recordings/` (KMS-encrypted, EPT-signed) |
| Secrets | AWS SSM `/windy-call/prod/*` → loaded into `.env.production` |
| DNS | Cloudflare zone `windycall.com` — `A api.windycall.com` → EC2 IP |

Container image is built from `service/` (not the repo root) — `pyproject.toml` and `app/` live there. `.github/`, `README.md`, `spec/` do not ship in the wheel.

---

## 2. Repo layout on the host

The deploy directory is **untracked** (matches the `windy-pro` and `eternitas` pattern per memory `feedback_windy_pro_deploy_layout.md`):

```
/opt/windy-call/
├─ deploy-prod/                  # untracked; pull from src/ periodically
│  ├─ docker-compose.yml         # copy of service/docker-compose.yml (lands in M0.7)
│  ├─ Caddyfile                  # reverse-proxy config (see §4)
│  ├─ .env.production            # SECRETS — never committed
│  └─ logs/                      # docker volume mount
└─ src/                          # `git clone https://github.com/sneakyfree/windy-call`
```

`deploy-prod/` is hand-curated. `src/` is the canonical Git working tree; deploys pull there, then copy relevant files into `deploy-prod/`.

---

## 3. `.env.production`

Required keys:

```sh
ENVIRONMENT=production
SERVICE_NAME=windy-call
LOG_LEVEL=INFO

# Redis (in-cluster Docker network)
REDIS_URL=redis://call-redis:6379/0

# Eternitas — platform key + webhook secret minted in eternitas admin
ETERNITAS_BASE_URL=https://api.eternitas.ai
ETERNITAS_JWKS_URL=https://api.eternitas.ai/.well-known/eternitas-keys
ETERNITAS_PLATFORM_API_KEY=et_plt_REDACTED
ETERNITAS_WEBHOOK_SECRET=REDACTED

# Cell client (number → passport lookups for inbound routing)
CELL_BASE_URL=http://cell-api:8800
CELL_INTERNAL_KEY=REDACTED
FALLBACK_OWNER_PASSPORT=ET26-WIND-Y000

# Twilio
TWILIO_ACCOUNT_SID=AC8459...
TWILIO_AUTH_TOKEN=REDACTED
TWILIO_FROM_NUMBER=+17542772201
TWILIO_WEBHOOK_BASE_URL=https://api.windycall.com   # inbound signature verification

# Cost cap (D.2 / D.2.2) — base $5/mo, scaled by EI tier
MONTHLY_COST_CAP_USD_DEFAULT=5.0
MONTHLY_COST_WARNING_PCT=0.80
```

All `REDACTED` values live in `~/kit-army-config/secrets/` per memory `reference_lockbox.md`. Pull via SSM Parameter Store to the EC2 host, never via git.

Per memory `feedback_pydantic_settings_list_env.md`, do NOT override `CORS_ORIGINS` via env — `pydantic-settings` JSON-decodes list-typed env vars and a comma-separated value crashes boot.

---

## 4. Caddyfile

Caddy runs on the host (binding 80/443 directly). `/opt/windy-call/deploy-prod/Caddyfile`:

```caddy
api.windycall.com {
    reverse_proxy 127.0.0.1:8600
    encode gzip zstd
    log {
        output file /var/log/caddy/api.windycall.com.log
        format json
    }
}
```

Per memory `feedback_caddy_inode_binding.md`, do NOT `cp` the Caddyfile (breaks bind mounts). Use `tee` or in-place edit. Capture host file + bind-mount path before edits.

---

## 5. Initial deploy

```sh
# 1. Clone canonical source
sudo mkdir -p /opt/windy-call && sudo chown ec2-user:ec2-user /opt/windy-call
cd /opt/windy-call
git clone https://github.com/sneakyfree/windy-call.git src

# 2. Provision deploy-prod/ from source + secrets
mkdir -p deploy-prod logs
cp src/service/docker-compose.yml deploy-prod/docker-compose.yml   # once M0.7 lands
# Edit deploy-prod/.env.production with values from kit-army-config/secrets/

# 3. Caddy
sudo tee /etc/caddy/Caddyfile < deploy-prod/Caddyfile
sudo systemctl reload caddy

# 4. First container build + boot
cd deploy-prod
docker compose --env-file .env.production up -d --build

# 5. Verify
curl -fsS https://api.windycall.com/health
# {"status":"ok","service":"windy-call",...}
```

---

## 6. Rolling deploy

Per memory `feedback_compose_restart_envfile.md`, `docker compose restart` reuses existing env block — new `.env` keys do NOT propagate. Always use `up -d --force-recreate` for env-changing deploys.

```sh
cd /opt/windy-call/src && git pull origin main

# Sync compose if it changed
cp service/docker-compose.yml ../deploy-prod/docker-compose.yml

cd ../deploy-prod
docker compose --env-file .env.production up -d --build --force-recreate

# Verify
curl -fsS https://api.windycall.com/health
docker compose --env-file .env.production logs --tail=50 call-api
```

When DB migrations land (M3+ audit log), chain `alembic upgrade head` per memory `feedback_manual_deploy_alembic.md`:

```sh
docker compose --env-file .env.production up -d --build --force-recreate
docker compose exec call-api alembic upgrade head
```

---

## 7. Smoke tests after deploy

```sh
# 1. Liveness
curl -fsS https://api.windycall.com/health

# 2. Auth check (no EPT)
curl -i https://api.windycall.com/voice/voicemail?to=%2B17542772201
# HTTP/2 401

# 3. Auth check (with valid EPT — pull from kit-army-config/secrets/test-ept.txt)
curl -i -H "Authorization: Bearer ${TEST_EPT}" \
  "https://api.windycall.com/voice/voicemail?to=%2B17542772201"
# HTTP/2 200

# 4. Twilio webhook signature path (negative — bad signature → 403)
curl -i -X POST https://api.windycall.com/webhooks/twilio/voice \
  -H "X-Twilio-Signature: deadbeef" \
  -d "CallSid=CA1&From=%2B1&To=%2B1&CallStatus=ringing"
# HTTP/2 403
```

If 1–2 fail, **roll back** before debugging in prod (see §8).

---

## 8. Rollback

```sh
cd /opt/windy-call/src && git log --oneline -5  # find prior known-good SHA
git checkout <prior-sha>

cd /opt/windy-call/deploy-prod
docker compose --env-file .env.production up -d --build --force-recreate
curl -fsS https://api.windycall.com/health
```

After rollback succeeds, return to `src/` and `git checkout main`.

---

## 9. Common operations

| Task | Command |
|---|---|
| Tail logs | `docker compose --env-file .env.production logs -f call-api` |
| Restart (env unchanged) | `docker compose --env-file .env.production restart call-api` |
| Restart (env changed) | `docker compose --env-file .env.production up -d --force-recreate call-api` |
| Inspect container env | `docker compose exec call-api env \| grep -i twilio` |
| Redis inspection | `docker compose exec call-redis redis-cli ping` |
| Wipe cost-cap state | `docker compose exec call-redis redis-cli FLUSHDB` (DESTRUCTIVE) |
| Stop everything | `docker compose --env-file .env.production down` |

---

## 10. Pre-deploy checklist (from the laptop)

```sh
bash .github/lint/lint-canonical-domains.sh \
  --config .github/lint/canonical-domains.json .
cd service && uv run pytest && uv run ruff check app/ tests/
docker build -t windy-call:dryrun ./service
```

CI runs all four on every PR (`.github/workflows/ci.yml`). Don't deploy from a branch that hasn't passed CI.

---

## 11. Future sections (placeholder)

- **§12 (M2)** — Voice-provider abstraction deploy: Retell account + Vapi fallback config + provider-routing policy in `.env.production`.
- **§13 (M3)** — Audit log to Postgres: per-call schema, alembic migration runbook, retention policy.
- **§14 (M3+)** — Recording storage: S3 bucket policy, KMS key, EPT-sign-at-write, principal-consent gate.
- **§15 (M9)** — Public launch: capacity planning, STIR/SHAKEN attestation-A verification, A2P 10DLC sub-brand monitoring.

---

**End of M0 deployment guide.** Update with each milestone's deploy delta — don't let it drift behind production.
