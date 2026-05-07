# Windy Call

> **Every Windy Fly agent gets its own phone number.**
>
> Domain: [windycall.com](https://windycall.com) (Cloudflare, registered 2026-05-06)
> Status: foundation laid; v1 service in design

## What it is

Windy Call is the telephony layer of the Windy ecosystem. From the moment a Windy Fly agent hatches, it can be issued its own phone number — capable of sending and receiving SMS, answering calls, leaving and transcribing voicemails, and placing outbound calls on behalf of its human owner.

The product solves real human pain (privacy: nobody wants to give out their cell number), gives agents a real-world reach (text and call recipients who don't use Windy), and creates one of the strongest retention + monetization moats in the ecosystem ($99 one-time + $9.99/month).

## Why a separate repo (vs. baked into windy-pro)

We learned from the windy-translate-in-windy-pro entanglement: cross-cutting product features that get embedded inside the identity hub become painful to extract later. Windy Call is genuinely a discrete product:

- Discrete compliance surface (A2P 10DLC, TCPA, carrier reputation per number)
- Discrete cost model (per-SMS, per-minute, per-number)
- Discrete webhook surface (incoming SMS/voice → service → agent)
- Discrete pricing SKU (standalone OR bundled with Pro/Ultra/Max)
- Eventually sellable standalone (agent-with-phone-number for non-Windy customers)

Building it separately from day 1 keeps boundaries clean and makes the future graduation paths painless.

## The two flagship pillars Windy Call supports

The whole Windy ecosystem rests on two:

1. **The most polished voice-to-text platform anywhere** — frictionless capture of human intent
2. **The most agent-friendly ecosystem in the world** — frictionless agent execution

Windy Call is the *real-world reach* extension of pillar #2. It's how an agent can interact with humans who aren't on Windy yet.

## Three-phase product roadmap

| Phase | What ships | Effort | Audience |
|---|---|---|---|
| **1 — SMS only** | Inbound + outbound SMS via Twilio. Agent receives texts, drafts replies, sends via voice command or autonomously. Number provisioned at hatch. | ~3-4 weeks | Beta users in ballrooms |
| **2 — Voice answering** | Agent answers calls with its persona, transcribes both sides, plays AI-generated TTS responses. Voicemail transcription. | ~3-4 weeks after Phase 1 | All paid tiers |
| **3 — Outbound voice + advanced** | Agent places calls (book reservations, contact vendors). Multi-language voice. Group SMS / RCS where supported. | ~6-8 weeks after Phase 2 | Premium tiers |

## The Eternitas + EII integration

Windy Call is an **event source** for the Eternitas Integrity Index. Every action signed by the agent's Eternitas passport, every outcome reported back to update the score:

- **Successful conversation** (recipient replies positively, no block) → reliability +5
- **Recipient marks as spam** → compliance -30
- **TCPA violation report** → compliance -100 + auto-throttle
- **Voicemail with positive callback** → reputation +10
- **Successful appointment booked via outbound call** → reliability +20

Per-EII rate limit table (default; platforms can override):

| EII | Band | Inbound SMS/day | Outbound SMS/day | Voice calls/day |
|---|---|---|---|---|
| 900-1000 | Exceptional | unlimited | 200 | 50 |
| 750-899 | Good | unlimited | 50 | 20 |
| 600-749 | Fair | unlimited | 20 | 10 |
| 400-599 | Poor / cold-start | unlimited | 5 contacts only | 2 contacts only |
| <400 | Critical | unlimited | suspended | suspended |

A newborn agent at EII 500 starts in "Poor / cold-start" — can text contacts the user already texts from, can't cold-outreach. After 30 days of clean behavior → score climbs to 700+ → cold-outreach unlocks. Self-regulating.

## Compliance + abuse defense (multi-layer)

We're handling regulated telecom — TCPA, FCC, A2P 10DLC are real. Strategy from day 1:

1. **ToS waiver** at signup — user accepts responsibility for content
2. **A2P 10DLC** — each user with an agent number registers as a brand + campaign (compliance burden documented + automated where possible)
3. **EII rate limits** — automated throttling of bad-behaving agents (the genuine moat — see Eternitas Integrity Index docs)
4. **Carrier-reputation tracking** — aggregate spam reports per number; suspend at threshold
5. **Cyber/E&O insurance** — umbrella covers residual liability
6. **Auto-takedown** — abuse reports trigger immediate suspension pending review (Twilio loves us for this)

Stripe-style "smart defaults that prevent most abuse, the rest is on the user." Not "we worry about TCPA."

## Architecture (v1)

```
┌────────────────────────────────────────────────────────────────────────┐
│ AGENT (Windy Fly) — calls Windy Call as a tool                         │
│                                                                        │
│   Voice command from user: "Text Bob: I'll be 10 min late"             │
│   ↓                                                                    │
│   Windy Word transcribes + cleans                                      │
│   ↓                                                                    │
│   Agent's tool call: send_sms(recipient="+1...", body="...")           │
│   ↓                                                                    │
│   POST https://api.windycall.com/v1/sms/send                           │
│   Authorization: Eternitas <signed-EPT-JWT>                            │
│   ↓                                                                    │
│ ┌────────────────────────────────────────────────────────────────────┐ │
│ │ windy-call-service                                                 │ │
│ │  • Verifies EPT JWT (Eternitas JWKS)                               │ │
│ │  • Looks up agent's phone number from passport claim               │ │
│ │  • Checks EII score → applies rate limit                           │ │
│ │  • Verifies recipient is permitted (contact list / cold-outreach)  │ │
│ │  • Twilio Messages API → SMS sent                                  │ │
│ │  • Logs event to Eternitas (reliability +5 if delivered)           │ │
│ │  • Logs to Comms Hub (Windy Chat) so user sees outbound msg        │ │
│ │  • Returns delivery status                                         │ │
│ └────────────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────┘
```

## Endpoints (v1 spec — see `spec/agent-call-protocol.md`)

| Endpoint | Purpose |
|---|---|
| `POST /v1/numbers/provision` | Provision a Twilio number for an agent (called from hatch ceremony) |
| `GET /v1/numbers` | List user's agent phone numbers |
| `POST /v1/numbers/{id}/transfer` | Number portability — port out to another carrier |
| `POST /v1/sms/send` | Agent sends an SMS |
| `POST /v1/voice/call` | Agent places a voice call |
| `GET /v1/conversations` | Conversation history per number |
| `POST /v1/twilio/webhooks/sms` | Twilio incoming SMS webhook (Twilio → us) |
| `POST /v1/twilio/webhooks/voice` | Twilio incoming voice webhook (TwiML response) |
| `POST /v1/twilio/webhooks/voicemail` | Twilio voicemail recording webhook |

## Pricing (placeholder until master pricing review)

- **$99 one-time + $9.99/month standalone** (for users on Free tier wanting just an agent number)
- **Bundled with Ultra ($199/yr) and Max ($299/yr) tiers** at no additional cost
- **Number portability**: included; user can port out anytime if they cancel

## The killer scenarios (marketing)

**Scenario 1 — Privacy moat:**
> *"Hey, what's your number?"*
> *"Use my AI assistant: 555-WINDY-AI. It'll route the message to me."*

**Scenario 2 — Productivity moat:**
> Agent answers a robocall, listens, classifies as spam, blocks the number, never bothers user.

**Scenario 3 — The grandma demo:**
> Grandma's just-hatched agent uses its new number to text grandson Bob the marketing plan it just researched. Bob's phone shows "Grandma's AI" — the agent introduces itself, sends the link, asks for feedback.

## Ecosystem position

Windy Call is product #11 in the Windy family (12th counting Eternitas):

1. Windy Word — voice-to-text core
2. Windy Chat — unified comms hub
3. Windy Mail — email
4. Windy Cloud — storage
5. Windy Clone — voice clone marketplace
6. Windy Code — agent's operating environment
7. Windy Fly — the agents themselves
8. Windy Translate — translation models + API + marketplace
9. Windy Traveler — consumer travel companion
10. Windy Search — agent-centric web access
11. **Windy Call — agent telephony** (this repo)
12. Eternitas — identity + trust registry (third-party / shared)

## Status

- ✅ Domain registered (windycall.com on Cloudflare 2026-05-06)
- ✅ Repo + scaffolding + canonical-domains lint vendored
- ⏳ Service implementation (Phase 1 next)
- ⏳ Twilio account setup + A2P 10DLC compliance
- ⏳ Eternitas event-ingestion integration
- ⏳ Comms Hub conversation aggregation

## License

TBD. Service code likely Windy proprietary; spec + protocol open (intent: third-party platforms can build interoperable agent-telephony services using the same patterns).
