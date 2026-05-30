# L1 SMS Phone Ingestion — Design (v2)

**Project:** Apteker Realty — AI Issue Router (Phase 1)
**Author:** Tal
**For:** Nathalie Apteker
**Date:** 2026-05-21
**Status:** Revised draft for review (supersedes v1 of 2026-05-21)

### Changes from v1
- Poller demoted to a recovery loop; the happy-path handoff is now the webhook
  pushing directly into the in-process L2 pipeline. (§3, §6.2)
- Route renamed `/webhooks/sms` → `/webhooks/sms_phone` to avoid collision with
  the existing Twilio inbound route in `app/ingestion/inbound.py`. (§5.1, §6.1)
- `msg_id` is now derived deterministically as
  `sha256(from_number || body || received_at_phone)` so iPhone retries are
  actually de-duplicated. (§4)
- JSON field renamed `from` → `from_number` (Python keyword shadow). (§5.1)
- §5.2 "Daily backstop" rewritten honestly: iOS Shortcuts cannot read message
  history programmatically, so there is no automatic backstop in Phase 1. The
  residual 2–5% miss rate (phone fully off, low-power deferral, automation
  paused) is documented as an accepted gap. ADR-001 captures the Mac-agent
  option as the deferred path to close it.
- Hour estimate updated from 11 h → 14–16 h once replay UI, secret-rotation
  script, install guide, and honesty-driven test coverage are included. (§10)

---

## 1. Goal

Add a new L1 ingestion path that imports every inbound SMS arriving on
Nathalie's personal iPhone into the pipeline, where the existing L2 normalizer
picks it up and runs the full L2–L6 flow already in production.

A recovery service runs every `SMS_POLL_INTERVAL_SECONDS` (configurable,
default 30 s) and drains any rows the real-time path couldn't hand off.

## 2. Options evaluated

Three paths were considered:

### 2.1 Pull directly from the phone over the network
Not viable. Neither iOS nor Android exposes a remote API that lets a backend log in
to a personal phone and pull SMS. Anything that looks "direct" either (a) requires
USB-tethered ADB to a host the service can reach, or (b) silently reduces to an
on-phone agent — i.e. Option 2.3.

iCloud-private-API tooling (Reincubate `ricloud`, etc.) is unsupported,
contractually disallowed by Apple, and Apple has publicly committed to terminating
the underlying endpoints in **June 2026**. Not a basis for a production service.

### 2.2 AT&T website using Nathalie's account
Dead. The consumer-facing `messages.att.net` portal was shut down **Oct 31, 2019**.
The AT&T Messages Backup & Sync mobile app was retired **Dec 4, 2024** and the
cloud-stored history was deleted at sunset. As of 2026 AT&T offers no consumer
endpoint that mirrors a personal AT&T line's SMS history for retrieval. The only
AT&T programmatic SMS surface today is **AT&T 10DLC / Business Messaging**, which
requires the messages to be addressed to a business short/long code that we
provision — not Nathalie's personal mobile number. Eliminated.

### 2.3 On-phone forwarder copying every SMS to a destination we control
The only viable path. Selected sub-variant after kick-off discussion:

> **iOS + self-hosted webhook.**
> Nathalie's iPhone runs an Apple Shortcuts automation that fires on every
> received message and POSTs the message to a FastAPI endpoint we own.
> No third party is in the data path. Apple's Shortcuts is a first-party,
> App-Store-policy-compliant mechanism — no jailbreak, no private API.

Rejected sub-variants and why:
- **Email bridge (forward each SMS to a dedicated inbox, route through an
  email-channel adapter):** cheaper to implement but adds one external
  hop and 5–30 s of email-delivery jitter. Out of scope after Phase 1
  channel pruning (Gmail inbound was removed — see §11).
- **Port number to Twilio:** carrier-grade reliability, but porting a
  personal number is disruptive and requires a separate client
  decision. Out of scope for Phase 1.

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│ Nathalie's iPhone                                                │
│                                                                  │
│   Apple Shortcuts automation                                     │
│   Trigger: "When I get a message"                                │
│   Action:  Get Contents of URL                                   │
│           POST https://<router>/webhooks/sms_phone               │
│           Headers: Authorization: Bearer <token>                 │
│           Body:    JSON { from_number, body, received_at_phone } │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
                                 ▼  (real-time push)
┌──────────────────────────────────────────────────────────────────┐
│ L1 SMS INGESTION (new)                                           │
│                                                                  │
│  app/ingestion/sms_phone.py                                      │
│  ┌────────────────────────────┐                                  │
│  │ POST /webhooks/sms_phone   │                                  │
│  │  • bearer-token verify     │                                  │
│  │  • compute deterministic   │                                  │
│  │    msg_id                  │                                  │
│  │  • INSERT … ON CONFLICT    │                                  │
│  │    DO NOTHING (idempotent) │                                  │
│  │  • on insert: enqueue      │                                  │
│  │    pipeline.handle_inbound │                                  │
│  │    via FastAPI BackgroundTasks                                │
│  │  • return 200 quickly      │                                  │
│  └─────────────┬──────────────┘                                  │
│                │                                                 │
│                ├── happy path ──▶  pipeline.handle_inbound(...)  │
│                │                   on success: status='handed_off'│
│                │                                                 │
│                ▼                                                 │
│   ┌──────────────────────────┐                                   │
│   │ sms_inbox_raw  (staging) │                                   │
│   │  PK msg_id               │                                   │
│   │  status                  │                                   │
│   └────────────┬─────────────┘                                   │
│                │                                                 │
│  app/jobs/sms_poll.py  ◀──── RECOVERY LOOP ONLY                  │
│  ┌──────────────────────────────┐                                │
│  │ APScheduler                  │   SELECT … WHERE status='new'  │
│  │ every SMS_POLL_INTERVAL_SEC  │   AND received_at < now()-30s  │
│  │   (default 30, env-var)      │   FOR UPDATE SKIP LOCKED       │
│  └──────────────┬───────────────┘   LIMIT SMS_POLL_BATCH_SIZE    │
│                 │                                                │
│                 ▼ for each: pipeline.handle_inbound(...)         │
│  on success: UPDATE status='handed_off', handed_off_at=now()     │
│  on failure: attempts += 1; attempts>=N → 'dead_letter'          │
└──────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
                      existing L2 → L3 → L4 → L5 → L6
                      (no change to those layers)
```

### Why staging + recovery instead of pure synchronous push
- **Real-time latency** when L2+ are healthy: webhook → BackgroundTasks →
  `handle_inbound` runs in the same process, single-digit milliseconds.
- **Survives pipeline restarts** without losing in-flight SMS — the webhook
  always inserts the row first; if the background task is killed mid-flight,
  the recovery loop catches it.
- **Configurable cadence preserved.** `SMS_POLL_INTERVAL_SECONDS` still
  exists, still tunable. It governs how aggressively we sweep for stuck rows,
  not the primary handoff.
- **Dead-letter rows** stay in `sms_inbox_raw` with a retry count, visible in
  the dashboard for manual replay.

## 4. Data model

New table:

```sql
CREATE TABLE sms_inbox_raw (
    msg_id            TEXT PRIMARY KEY,   -- sha256(from_number||body||received_at_phone)
    from_number       TEXT NOT NULL,      -- E.164
    to_number         TEXT,               -- Nathalie's number (multi-line forward-compat)
    body              TEXT NOT NULL,
    received_at_phone TIMESTAMPTZ NOT NULL,   -- timestamp from the iPhone
    received_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    status            TEXT NOT NULL DEFAULT 'new',
                                          -- 'new' | 'handed_off' | 'dead_letter'
    attempts          INT  NOT NULL DEFAULT 0,
    last_error        TEXT,
    handed_off_at     TIMESTAMPTZ
);
CREATE INDEX sms_inbox_raw_status_idx
    ON sms_inbox_raw (status, received_at)
    WHERE status = 'new';
```

`msg_id` collision = idempotent no-op (`INSERT … ON CONFLICT DO NOTHING`). The
deterministic hash means an iPhone retry of the same message produces the same
`msg_id`, so de-duplication actually works. (In v1 we relied on a
Shortcut-generated UUID, which changes per Run and would have leaked
duplicates.)

## 5. On-phone setup (iOS Shortcuts)

### 5.1 Per-message automation
- **Personal Automation → Message → Any Sender → Run Immediately ON**
  (`Run Immediately` lands the trigger without a confirmation tap; available on
  iOS 15+.)
- Actions:
  1. Get Contents of `https://router.apteker.proactiverev.com/webhooks/sms_phone`
  2. Method: POST, Headers: `Authorization: Bearer <token>`,
     `Content-Type: application/json`
  3. Body (JSON):
     ```json
     {
       "from_number":       "<Shortcut variable: Sender>",
       "body":              "<Shortcut variable: Message>",
       "received_at_phone": "<Shortcut variable: Current Date, ISO 8601>"
     }
     ```
     (`msg_id` is computed server-side from these three fields.)

### 5.2 No automatic daily backstop in Phase 1

iOS Shortcuts cannot read message history from the Messages app — there is no
"Get Latest Messages" action. The v1 design promised a 03:00 backstop; that
section was incorrect. The realistic options to close the gap are tracked in
ADR-001 (see §9) and are out of scope for Phase 1.

**Accepted residual risk for Phase 1:** ~2–5% of inbound SMS may be missed when

- the phone is fully powered off, or
- iOS defers the automation under sustained low-power conditions, or
- Nathalie has paused her automations (intentionally or by accident).

The dashboard's L1 health panel (§6.5) will surface "messages-per-hour"
trending so a multi-hour gap is visible and we can ask her to check her
automation. A manual replay endpoint (§6.6) lets her or us inject any message
she sees on her phone that didn't make it to the pipeline.

### 5.3 Installable Shortcut delivery
A signed installable Shortcut is delivered separately. Token rotation is
zero-downtime: the server accepts both `SMS_BEARER_TOKEN_PRIMARY` and
`SMS_BEARER_TOKEN_SECONDARY`; rotate primary, push new Shortcut to her phone,
then retire secondary on her next sync.

## 6. Service components

### 6.1 `app/ingestion/sms_phone.py`
FastAPI router mounted at `/webhooks/sms_phone`.
- Verifies `Authorization: Bearer <token>` against `SMS_BEARER_TOKEN_PRIMARY`
  or `SMS_BEARER_TOKEN_SECONDARY` (constant-time compare).
- Parses JSON, normalises `from_number` to E.164.
- Computes `msg_id = sha256(from_number || '\x1f' || body || '\x1f' ||
  received_at_phone_iso).hexdigest()`.
- `INSERT INTO sms_inbox_raw (...) VALUES (...) ON CONFLICT (msg_id) DO NOTHING
  RETURNING msg_id`. If RETURNING yields a row, this is a new message —
  schedule the L2 handoff via `BackgroundTasks.add_task(...)`. If RETURNING
  yields nothing, the message was a duplicate — return 200 silently.
- Returns `200 {"status":"queued","msg_id":"…","duplicate":<bool>}`.
- Rejects with 401 on bad bearer, 400 on schema failure, 429 if the per-IP
  bucket is exhausted (defence against a stolen token).
- **In-process handoff** runs inside `BackgroundTasks`, so the HTTP response
  doesn't block on it. On success, the background task UPDATEs
  `status='handed_off'`. On failure, it leaves the row at `status='new'` and
  lets the recovery loop retry it.

### 6.2 `app/jobs/sms_poll.py` — recovery loop only
APScheduler job, registered alongside the existing P0 sweep in
`app/main.py`'s lifespan:
```python
scheduler.add_job(
    sms_poll.drain_stuck,
    "interval",
    seconds=config.SMS_POLL_INTERVAL_SECONDS,
    id="sms_poll",
    max_instances=1,
    coalesce=True,
)
```
Each tick targets *stuck* rows only — rows still at `status='new'` more than
`SMS_STUCK_THRESHOLD_SECONDS` after `received_at` (default 30 s). The healthy
in-process handoff never leaves a row stuck for that long.

1. `SELECT … FROM sms_inbox_raw
    WHERE status='new' AND received_at < now() - INTERVAL ':threshold seconds'
    ORDER BY received_at
    LIMIT :batch FOR UPDATE SKIP LOCKED`
2. For each row, build the `Inbound` envelope and call
   `pipeline.handle_inbound(channel='sms_phone', …)`.
3. On success: `UPDATE status='handed_off', handed_off_at=now()`.
4. On exception: `attempts += 1, last_error=…`; if `attempts >=
   SMS_POLL_MAX_ATTEMPTS` (default 5), `status='dead_letter'`.

`max_instances=1` + `coalesce=True` prevents overlapping ticks and storm-of-ticks
after a long pause.

### 6.3 `app/config.py` additions
```python
SMS_POLL_INTERVAL_SECONDS  = int(os.getenv("SMS_POLL_INTERVAL_SECONDS", "30"))
SMS_POLL_BATCH_SIZE        = int(os.getenv("SMS_POLL_BATCH_SIZE", "50"))
SMS_POLL_MAX_ATTEMPTS      = int(os.getenv("SMS_POLL_MAX_ATTEMPTS", "5"))
SMS_STUCK_THRESHOLD_SECONDS= int(os.getenv("SMS_STUCK_THRESHOLD_SECONDS", "30"))
SMS_BEARER_TOKEN_PRIMARY   = os.getenv("SMS_BEARER_TOKEN_PRIMARY")   # required
SMS_BEARER_TOKEN_SECONDARY = os.getenv("SMS_BEARER_TOKEN_SECONDARY") # optional
```

### 6.4 `app/pipeline.py` integration
No structural change. Both the webhook's BackgroundTask and the recovery loop
call the same `handle_inbound()` that the existing Twilio webhook calls.
`channel='sms_phone'` propagates to L4 so rules can tell phone-mirrored SMS
apart from a future Twilio-direct path.

### 6.5 Dashboard L1 health panel
- **In-flight:** rows with `status='new'` (should hover at 0 — non-zero >30 s
  means the recovery loop is doing real work).
- **Handed off (last 1 h):** count and msgs/min trend.
- **Dead-letter:** count + list with replay button.
- **No-traffic alert:** if msgs/hour falls to 0 during 08:00–19:00 ET on a
  weekday, flag — likely automation paused or phone off.

### 6.6 Admin endpoints
- `POST /admin/sms_inbox_raw/{msg_id}/replay` — re-runs `handle_inbound` on a
  dead-lettered row, resets `attempts=0`, `status='new'`. Auth: existing
  admin-bearer.
- `POST /admin/sms_inbox_raw/inject` — accepts `{from_number, body,
  received_at_phone}`, computes msg_id, inserts as new. Used when Nathalie
  spots a missed message on her phone and asks us to backfill it. Auth:
  same.

## 7. Security

- **Bearer token** in `Authorization` header, two-token rotation window for
  zero-downtime secret rotation (`SMS_BEARER_TOKEN_PRIMARY` and `_SECONDARY`).
- **TLS-only** endpoint, HSTS enforced.
- **No SMS body in logs.** The webhook logs `msg_id`, `from_number`, length,
  and a hash of the body — never the body itself. Same redaction L2 already
  does.
- **PII redaction** still happens in L2 (`normalize.py`) — unchanged.
- **Secret storage:** bearer tokens live in Render's secret store, not in
  `.env` files.
- **Why bearer instead of HMAC:** iOS Shortcuts has no built-in HMAC-SHA256
  action. Computing HMAC would require Scriptable.app or similar, adding
  install friction and a 3rd-party dependency on her phone. Bearer + TLS is
  the realistic security level for a single-installation use case.

## 8. Failure modes & guards

| Failure | Guard |
|---|---|
| iPhone fully off / Shortcut paused / iOS defer | Documented residual 2–5% miss rate; dashboard surfaces low-traffic gap; manual replay via `/admin/sms_inbox_raw/inject` |
| Webhook POST fails (network) | Shortcuts retries with backoff; deterministic msg_id makes it idempotent |
| Webhook accepts but DB insert fails | Returns 5xx → Shortcuts retries |
| Webhook inserts but BackgroundTask crashes mid-handoff | Recovery loop picks it up after `SMS_STUCK_THRESHOLD_SECONDS` |
| Pipeline (L2+) is down | Rows pile up in `sms_inbox_raw` at `status='new'`; recovery loop drains on restart |
| Bad/forged POST | Bearer mismatch → 401, ignored |
| Burst (50+ msgs/sec) | BackgroundTasks queue absorbs; recovery loop batches stragglers up to `SMS_POLL_BATCH_SIZE` |
| Parse error in pipeline for a specific row | After `SMS_POLL_MAX_ATTEMPTS`, moves to `dead_letter` and surfaces in dashboard |
| Stolen bearer token | Rotate primary, secondary covers in-flight requests, retire after re-install on her phone |

## 9. Open decisions & ADRs

### Open decisions (kick-off list)
- **R8.** Confirm Nathalie's phone is on iOS ≥ 15 (needed for "Run
  Immediately" Shortcuts on Message triggers). _(Assumed yes.)_
- **R9.** Should iMessages-from-other-Apple-users also be captured? Apple's
  Message trigger fires on both SMS and iMessage, so default is **yes**;
  filter to SMS only if she prefers.
- **R10.** Acceptance criterion: is a 2–5% residual miss rate acceptable for
  Phase 1, with the gap to be closed in a Phase 2 Mac-agent (ADR-001)?
- **R11.** Numbers to include — single device (her primary cell) at first;
  multi-device adds a `device_id` column later.

### ADR-001 — How to close the 2–5% miss rate (deferred)
Three candidate approaches when we decide to invest:
1. **Mac chat.db reader.** Small daemon on her office Mac reads
   `~/Library/Messages/chat.db` (Messages-in-iCloud-synced) every X seconds,
   POSTs deltas to the same `/webhooks/sms_phone` endpoint. Most reliable,
   no carrier change. ~1 day to build.
2. **Email bridge.** Install a forwarder that sends every SMS to a
   dedicated inbox; the existing email adapter ingests it. Lowest dev
   cost (~2 h), 5–30 s extra latency, one external hop in the data
   path. **(Deferred / out of scope after Gmail channel was dropped.)**
3. **Port to Twilio.** Carrier-grade, sub-second, but disruptive — separate
   client decision and number-port logistics.

## 10. Implementation plan

| # | Task | Est. |
|---|---|---|
| 1 | `sms_inbox_raw` migration | 0.5 h |
| 2 | `app/ingestion/sms_phone.py` (bearer auth, deterministic msg_id, BackgroundTasks handoff) | 3 h |
| 3 | `app/jobs/sms_poll.py` (recovery loop, SKIP LOCKED, dead-lettering) | 2 h |
| 4 | Wire into `app/main.py` lifespan + `app/config.py` env vars | 0.5 h |
| 5 | Admin endpoints: `/admin/sms_inbox_raw/{msg_id}/replay`, `/inject` | 1.5 h |
| 6 | Dashboard L1 health panel + dead-letter list/replay UI | 2 h |
| 7 | iOS Shortcut: design, build, sign, install on Nathalie's phone | 2 h |
| 8 | Install + token-rotation runbook (markdown) | 1 h |
| 9 | Tests: bearer auth, deterministic-id idempotency, BG handoff happy path, recovery-loop catches stuck rows, dead-letter threshold, replay endpoint | 2.5 h |
| 10 | Add a phone-SMS scenario to `09_Advanced_Tests/` | 1 h |
| | **Total** | **16 h** |

Phase 1 contract budget: 39.3 h. Used to date: ~26 h. Remaining: ~13 h.
**16 h overruns by ~3 h.** Recommendation: defer the dashboard panel (#6, 2 h)
and the advanced-test scenario (#10, 1 h) to a Phase 1.1 follow-up, landing
the core path inside 13 h and shipping the rest in a small follow-on.

## 11. References

- AT&T Messages Backup & Sync sunset:
  <https://www.att.com/features/backup-sync/>
- AT&T Messages app discontinuation (Dec 4, 2024):
  <https://www.phonearena.com/news/AT-T-shuts-down-its-messaging-apps-prematurely-and-deletes-cloud-backups_id165863>
- iOS Shortcuts communication triggers:
  <https://support.apple.com/guide/shortcuts/communication-triggers-apdd711f9dff/ios>
- Forward SMS to webhook with iPhone Shortcuts (worked example):
  <https://dev.to/noha1337/forward-sms-to-webhook-with-iphone-shortcut-automations-4d6>
- SMS Gateway for Android (capcom6) — kept on file as future Android fallback:
  <https://github.com/capcom6/android-sms-gateway>
- Reincubate `ricloud` and Apple's June 2026 deprecation of private-API
  iMessage access: <https://reincubate.com/ricloud-api/>
