# Windy Call Agent Protocol — v0.1 (DRAFT)

> Status: drafting — captures the architecture + conventions discussed 2026-05-06. Subject to revision in V2 refinement.

## Scope

This document specifies the HTTP wire protocol for the Windy Call v1 service: how an agent invokes telephony tools (send SMS, place call), how Twilio webhooks reach the service, and how authentication, rate limits, EII integration, and audit logging interact.

## Authentication

Two distinct auth surfaces:

**1. Agent-to-service** (when an agent calls our API):

```
Authorization: Eternitas <signed-EPT-JWT>
```

Same EPT JWT pattern as Windy Search. Verified via Eternitas JWKS at `https://api.eternitas.ai/.well-known/eternitas-keys`. Reject if `rev != null` or `exp` past.

**2. Twilio-to-service** (when Twilio webhooks call us):

```
X-Twilio-Signature: <HMAC-SHA1 of full URL + sorted POST params, with auth token as key>
```

Verified per Twilio's request validation spec. Webhooks without a valid signature return 403.

## Endpoints (v1)

### `POST /v1/numbers/provision`

Called by windy-pro account-server during hatch ceremony, after the user pays the $99 upsell.

**Auth:** service-to-service (account-server's `et_plt_*` API key)

**Request:**
```json
{
  "passport": "ET-XYZ...",
  "owner_passport": "EH-ABC...",
  "country": "US",
  "area_code_preference": "512"
}
```

**Response:**
```json
{
  "number_id": "num_...",
  "phone_number": "+15125551234",
  "country": "US",
  "provisioned_at": "2026-05-06T...",
  "monthly_cost_usd": 1.15
}
```

Side effects:
- Twilio number provisioned via API
- DB record created linking phone_number → agent passport → owner passport
- Twilio webhooks for SMS + voice configured to point at our service
- A2P 10DLC campaign registration initiated (async — may take 1-3 weeks)
- Eternitas event: `integrity.event` with `event_type=number_provisioned`

### `GET /v1/numbers`

List all phone numbers owned by the calling user.

**Auth:** EPT JWT

**Response:**
```json
{
  "numbers": [
    {
      "number_id": "num_...",
      "phone_number": "+15125551234",
      "agent_passport": "ET-...",
      "agent_name": "Aria",
      "provisioned_at": "...",
      "monthly_cost_to_date_usd": 4.27,
      "compliance_status": "registered"
    }
  ]
}
```

### `POST /v1/numbers/{id}/transfer`

Initiate a port-out request. User can take their agent's number to another carrier.

**Auth:** EPT JWT (must own this number)

**Request:**
```json
{
  "target_carrier": "Verizon",
  "loa_signed": true,
  "destination_account_info": "..."
}
```

**Response:** 202 Accepted with port-out tracking ID. Process completes in 1-7 business days per FCC rules.

### `POST /v1/sms/send`

Agent sends an SMS.

**Auth:** EPT JWT (passport must own a phone number)

**Request:**
```json
{
  "recipient": "+15555550100",
  "body": "Hi, this is Grant's AI assistant. Your appointment is confirmed for...",
  "media_urls": []
}
```

**Server-side logic:**
1. Verify EPT JWT
2. Look up agent's phone number
3. Check EII score → apply rate limit per band (see README table)
4. Verify recipient is permitted:
   - If recipient has texted the agent's number before → always permitted
   - Else → require EII ≥ 600 (cold-outreach gate)
5. Apply per-month cost cap (don't exceed user's tier limit)
6. Twilio Messages API call
7. Log Eternitas event: `integrity.event` `event_type=sms_sent`, `dimension=reliability`, `delta_hint=+1`
8. Mirror to Windy Chat Comms Hub (so user sees outbound message in unified thread)

**Response:**
```json
{
  "message_sid": "SM...",
  "status": "queued",
  "cost_usd": 0.0079
}
```

### `POST /v1/voice/call`

Agent places an outbound voice call.

**Auth:** EPT JWT, EII ≥ 700 (per spec table)

**Request:**
```json
{
  "recipient": "+15555550100",
  "purpose": "Book a table for 2 at Chez Foo for tomorrow 7pm. Use the agent's persona.",
  "max_duration_seconds": 300
}
```

**Server-side logic:**
1. Verify auth + EII gate
2. Twilio Calls API: initiate outbound call
3. TwiML response: agent's persona answers via TTS (using user's Windy Clone voice if configured, else default)
4. Real-time conversation streams to LLM (via Pro broker token)
5. LLM generates responses, Twilio TTS plays them
6. Recording saved to Windy Cloud
7. Transcript saved + emailed to user (via Windy Mail)
8. Eternitas events for outcome (success → +20, hung up on → -5, etc.)

**Response:** 202 with call tracking ID. Real-time updates via Server-Sent Events on `GET /v1/calls/{id}/stream`.

### `GET /v1/conversations`

Conversation history per number, paginated.

**Auth:** EPT JWT

**Response:** standard paginated list of message + call records, mirroring what's also visible in Windy Chat Comms Hub.

### Webhook endpoints (Twilio → us)

#### `POST /v1/twilio/webhooks/sms`

Incoming SMS handler.

**Auth:** Twilio signature verification (`X-Twilio-Signature`)

**Server-side logic:**
1. Verify signature; reject if invalid
2. Look up agent by `To` phone number
3. Decode `From`, `Body`, optional media
4. Dispatch to agent's persona via Windy Fly (with full context: sender history, agent memory, owner instructions)
5. Agent generates response (or decides to forward to owner)
6. If responding: Twilio Messages API outbound (or queue for later if rate-limited)
7. Mirror to Windy Chat Comms Hub
8. Eternitas event for the inbound outcome

**Response:** TwiML (empty, just 200 OK to Twilio)

#### `POST /v1/twilio/webhooks/voice`

Incoming voice call handler.

**Auth:** Twilio signature verification

**Server-side logic:**
1. Verify signature
2. Return TwiML response that:
   - Plays a greeting (agent's persona — "Hi, this is [Owner]'s AI assistant")
   - Records the conversation
   - Streams audio to a real-time speech-to-text service (Whisper or Twilio's Speech)
   - Routes transcript to LLM, generates response
   - Twilio TTS plays response
   - Loop until caller hangs up
3. After call: save recording to Windy Cloud, save transcript to user's vault, send summary to user via preferred channel

**Response:** TwiML XML

#### `POST /v1/twilio/webhooks/voicemail`

Voicemail recording handler (when call goes to voicemail).

Server-side: transcribe via Whisper, save to Cloud, notify user via Comms Hub.

## Cost caps (per-tier monthly)

| Tier | Monthly cap | Behavior at cap |
|---|---|---|
| Free + agent number standalone | $5 | Hard reject SMS/voice, allow inbound only |
| Pro tier | $25 | Soft warning at 80%, hard reject at 100% |
| Ultra tier | $75 | Soft warning at 80%, soft cap (allow with notification) at 100% |
| Max tier | $200 | Soft warning, no hard cap |

Cost includes: SMS sends ($0.0079 each), inbound SMS ($0.0079 each — Twilio bills inbound too), voice minutes ($0.0140/min), monthly number rental ($1.15/mo), A2P 10DLC fees ($1.50/mo).

## Anti-abuse defenses (in addition to EII rate limits)

- **Per-recipient daily cap** — agent can't text the same recipient more than 50 times/day (prevents harassment)
- **First-message identifier** — agent's first SMS to a new recipient must include "I'm [Owner]'s AI assistant" boilerplate
- **Opt-out compliance** — STOP/UNSUBSCRIBE keywords trigger automatic block + carrier-style auto-reply
- **Spam-report tracking** — aggregate per-number spam complaints; suspend at threshold (e.g., 5 reports in 7 days)
- **Cold-start floor** — newly-provisioned numbers can only text contacts the user has texted from for the first 7 days (regardless of EII)

## Open questions / TBD

- Voice for outbound calls: default voice vs user's Windy Clone — UX flow for selection
- Group SMS / RCS: prioritize for Phase 3 or defer
- International numbers: support from day 1 or US-only
- Number reuse: if user cancels and number returns to Twilio pool, what's the cool-down before reuse?
- Multi-agent: can a single number serve multiple agents (probably not — 1:1 relationship)
- Recording consent (two-party-consent states): how do we handle California, Florida, etc.?
- Call screening: agent answers, screens, forwards to user's real number when warranted — Phase 3 or Phase 1?
