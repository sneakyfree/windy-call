# Windy Call — repo notes for Claude

## What this repo is

Agent telephony for the Windy ecosystem. Every Windy Fly agent gets its own phone number from the moment of hatch — SMS in/out, voice calls in/out, voicemail transcription. See `README.md` for the full vision.

The thesis in one sentence: Windy Call is the toolkit a Windy Fly agent calls (and a service Twilio webhooks call into) so the agent can communicate with humans who don't yet use Windy.

## Branching policy

Same as the rest of the Windy ecosystem: feature branches + PR review. No direct commits to `main`. Squash-merge on land.

## Where things live

- `README.md` — vision + roadmap (read first)
- `spec/agent-call-protocol.md` — the HTTP wire protocol (in progress)
- `service/` — the v1 toolkit service (not yet started)
- `.github/lint/canonical-domains.{json,sh}` — vendored from kit-army-config; catches non-canonical domain references in PRs
- `.github/workflows/canonical-domains-lint.yml` — runs the lint on every push + PR

## Related repos in the ecosystem

- `windy-pro` — identity hub. Hatch ceremony there triggers number provisioning here. Stripe billing for $99 + $9.99/mo lives there.
- `eternitas` — trust registry. Windy Call reports score-affecting events here (successful conversations, spam reports, abuse).
- `windy-agent` (Windy Fly) — the agent runtime that calls Windy Call as a tool.
- `windy-chat` — Comms Hub. Conversations on agent numbers appear here in unified threads alongside SMS/Telegram/Discord/etc.
- `windy-search` — sister product (agent web access). Same architectural pattern; copy patterns from there when building Windy Call's service.

## When working on this repo

- Read `kit-army-config/canonical-domains.json` before adding any external URL — the lint will catch you if you slip a banned hostname.
- Eternitas Integrity Index events go to `POST /api/v1/integrity/events` in eternitas. Windy Call emits one event per outbound action + one per inbound outcome.
- The v1 service should be a stateless HTTP service (FastAPI, mirroring eternitas + windy-search choices) that agents OR Twilio webhooks call.
- Twilio webhooks must verify request signatures — never trust unsigned webhooks.
- Voice recordings + transcripts are stored in Windy Cloud (with retention policy). Don't store recordings in this repo's DB.
- Voice answer uses the agent's persona from Windy Fly (not configured in this repo). Defer voice synthesis choice to Windy Fly + Windy Clone.

## Compliance reminders

- Every user-facing flow that involves SMS or voice calls must include a TCPA acknowledgment.
- A2P 10DLC compliance is per-number; track per-number campaign registration status in the DB.
- Spam-report rate limits + auto-suspension are non-negotiable defenses against carrier blacklisting.
- Number portability (port-out) is a user right; don't gate it behind cancellation friction.

## Pre-launch checklist (when v1 is close)

- [ ] Twilio account in production (not trial)
- [ ] A2P 10DLC brand + campaign approved
- [ ] All 9 endpoints functional with EPT JWT auth
- [ ] EII rate limits enforced + tunable
- [ ] Twilio webhook signature verification mandatory
- [ ] Audit log → Eternitas integrity event ingest
- [ ] Cost monitoring per Eternitas-passport (hard cap on per-month spend)
- [ ] Number-portability runbook documented
- [ ] Cyber/E&O insurance policy in place
- [ ] Public spec at windycall.com/spec for any third parties wanting interop
