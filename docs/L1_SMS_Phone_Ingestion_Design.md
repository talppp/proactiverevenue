# L1 SMS Phone Ingestion — Design

**Project:** Apteker Realty — AI Issue Router (Phase 1)
**Author:** Tal
**For:** Nathalie Apteker
**Date:** 2026-05-21
**Status:** Draft for review

---

## 1. Goal

Add a new L1 ingestion path that imports every inbound SMS arriving on Nathalie's
personal cell into the pipeline's staging table, where the existing L2 normalizer
picks it up and runs the full L2–L6 flow already in production.

A service/batch runs every `SMS_POLL_INTERVAL_SECONDS` (configurable, default 30 s),
drains the staging table, and hands each row off to L2.

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
- **Email bridge (forward each SMS to Gmail, reuse our existing Gmail adapter):**
  cheaper to implement but adds one external hop and 5–30 s of email-delivery
  jitter. Kept as a fallback if the Shortcuts path proves flaky in field testing.
- **Port number to Twilio:** carrier-grade reliability, but porting a personal
  number is disruptive and requires a separate client decision. Out of scope for
  Phase 1.

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│ Nathalie's iPhone                                                │
│                                                                  │
│   Apple Shortcuts automation                                     │
│   Trigger: "When I get a message"                                │
│   Action:  Get Contents of URL                                   │
│           POST https://<router>/webhooks/sms                     │
│           Headers: X-PAR-SMS-Sig: <HMAC-SHA256 of body>          │
│           Body:    JSON { msg_id, from, body, received_at }      │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
                                 ▼  (real-time push)
┌──────────────────────────────────────────────────────────────────┐
│ L1 SMS INGESTION (new)                                           │
│                                                                  │
│  app/ingestion/sms_phone.py                                      │
│  ┌─────────────────────────┐    ┌──────────────────────────┐     │
│  │ POST /webhooks/sms      │───▶│ sms_inbox_raw  (staging) │     │
│  │  • HMAC verify          │    │  PK msg_id               │     │
│  │  • idempotent insert    │    │  status = 'new'          │     │
│  │  • returns 200 quickly  │    └────────────┬─────────────┘     │
│  └─────────────────────────┘                 │                   │
│                                              │                   │
│  app/jobs/sms_poll.py                        │                   │
│  ┌──────────────────────────────┐            │                   │
│  │ APScheduler                  │◀───────────┘                   │
│  │ every SMS_POLL_INTERVAL_SEC  │    SELECT … WHERE status='new' │
│  │   (default 30, env-var)      │    FOR UPDATE SKIP LOCKED      │
│  └──────────────┬───────────────┘    LIMIT SMS_POLL_BATCH_SIZE   │
│                 │                                                │
│                 ▼                                                │
│  for each row: pipeline.handle_inbound(channel='sms_phone', …)   │
│  on success: UPDATE status='handed_off', handed_off_at=now()     │
│  on failure: increment attempts; on attempts>=N -> 'dead_letter' │
└──────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
                      existing L2 → L3 → L4 → L5 → L6
                      (no change to those layers)
```

Why a polling loop on top of a push webhook:

- Decouples carrier/Shortcuts jitter from the pipeline; bursty bunches don't
  stampede L2.
- Survives pipeline restarts without losing in-flight SMS — the webhook just
  appends rows; the poller catches up.
- Gives a single tunable knob (`SMS_POLL_INTERVAL_SECONDS`) for the whole
  ingestion cadence, which is what the brief asks for.
- Failed rows stay in `sms_inbox_raw` with a retry count; dead-letter rows are
  visible in the dashboard for manual replay.

## 4. Data model

New table:

```sql
CREATE TABLE sms_inbox_raw (
    msg_id            TEXT PRIMARY KEY,        -- from forwarder; gen:<uuid> if absent
    from_e164         TEXT NOT NULL,
    to_e164           TEXT,                    -- Nathalie's number (for multi-line later)
    body              TEXT NOT NULL,
    received_at_phone TIMESTAMPTZ,             -- timestamp from the iPhone
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

`msg_id` collision = idempotent no-op (`INSERT … ON CONFLICT DO NOTHING`). This
protects us against the iPhone retrying a failed POST and against the daily
backstop re-sending messages it already delivered.

## 5. On-phone setup (iOS Shortcuts)

The Shortcut Nathalie installs has two parts:

### 5.1 Per-message automation
- **Personal Automation → Message → Any Sender → Run Immediately ON**
  (`Run Immediately` lands the trigger without a confirmation tap; available on
  iOS 15+.)
- Actions:
  1. Get Contents of `https://router.apteker.proactiverev.com/webhooks/sms`
  2. Method: POST, Headers: `X-PAR-SMS-Sig: <HMAC>`, `Content-Type: application/json`
  3. Body (JSON):
     ```json
     {
       "msg_id": "<Shortcut-generated UUID>",
       "from":   "<Shortcut variable: Sender>",
       "body":   "<Shortcut variable: Message>",
       "received_at": "<Shortcut variable: Current Date, ISO 8601>"
     }
     ```

### 5.2 Daily backstop
- **Personal Automation → Time of Day → 03:00 → Run Immediately ON**
- Action: read the last 24 h of messages from the Messages app and POST any
  whose `msg_id` we have not already received.
- Catches the rare case where the per-message automation didn't fire (phone
  off, deep sleep, automation paused). The staging table's PK constraint makes
  duplicates safe.

A signed installable Shortcut is delivered separately (the HMAC secret is
embedded; secret rotation = re-install).

## 6. Service components

### 6.1 `app/ingestion/sms_phone.py`
FastAPI router mounted at `/webhooks/sms`.
- Verifies `X-PAR-SMS-Sig` (HMAC-SHA256 of the raw body, secret from
  `SMS_WEBHOOK_SECRET` env var, constant-time compare).
- Parses JSON, normalises `from` to E.164.
- `INSERT INTO sms_inbox_raw … ON CONFLICT (msg_id) DO NOTHING`.
- Returns `200 {"status":"queued"}` quickly; never blocks on the pipeline.
- Rejects with 401 on bad signature, 400 on schema failure, 429 if the per-IP
  bucket is exhausted (defence against a stolen secret).

### 6.2 `app/jobs/sms_poll.py`
APScheduler job, registered alongside the existing P0 sweep in
`app/main.py`'s lifespan:
```python
scheduler.add_job(
    sms_poll.drain_once,
    "interval",
    seconds=config.SMS_POLL_INTERVAL_SECONDS,
    id="sms_poll",
    max_instances=1,
    coalesce=True,
)
```
Each tick:
1. `SELECT … FROM sms_inbox_raw WHERE status='new' ORDER BY received_at
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
SMS_POLL_INTERVAL_SECONDS = int(os.getenv("SMS_POLL_INTERVAL_SECONDS", "30"))
SMS_POLL_BATCH_SIZE       = int(os.getenv("SMS_POLL_BATCH_SIZE", "50"))
SMS_POLL_MAX_ATTEMPTS     = int(os.getenv("SMS_POLL_MAX_ATTEMPTS", "5"))
SMS_WEBHOOK_SECRET        = os.getenv("SMS_WEBHOOK_SECRET")   # required in prod
```

### 6.4 `app/pipeline.py` integration
No structural change. The poller calls the same `handle_inbound()` that the
existing Twilio webhook calls. `channel='sms_phone'` propagates to L4 so rules
can tell phone-mirrored SMS apart from a future Twilio-direct path.

### 6.5 Dashboard
Add a single panel: **Phone SMS staging** — count of rows in `new`,
`handed_off` (last hour), and `dead_letter`. The existing inbox view already
shows the cards once they hit L6; this panel is the L1 health check.

## 7. Security

- **HMAC** on the webhook, secret rotated yearly (or on suspected compromise).
- **TLS-only** endpoint, HSTS enforced.
- **No SMS body in logs.** The webhook logs `msg_id`, `from`, length, and a
  hash of the body — never the body itself. Same redaction L2 already does.
- **PII redaction** still happens in L2 (`normalize.py`) — unchanged.
- **Secret storage:** `SMS_WEBHOOK_SECRET` lives in Render's secret store, not
  in `.env` files.

## 8. Failure modes & guards

| Failure | Guard |
|---|---|
| iPhone offline / Shortcut paused | Daily 03:00 backstop replays last 24 h |
| Webhook POST fails (network) | Shortcuts retries with backoff; idempotent on PK |
| Webhook accepts but DB write fails | Returns 5xx → Shortcuts retries |
| Pipeline (L2+) is down | Rows pile up in `sms_inbox_raw`; poller catches up on recovery |
| Bad/forged POST | HMAC mismatch → 401, ignored |
| Burst (50+ msgs/sec) | Poller batches up to `SMS_POLL_BATCH_SIZE`, advances steadily |
| Stuck row (parse error in pipeline) | After `SMS_POLL_MAX_ATTEMPTS`, moves to `dead_letter` and surfaces in dashboard |

## 9. Open decisions (add to kick-off list)

- **R8.** Confirm Nathalie's phone is on iOS ≥ 15 (needed for "Run
  Immediately" Shortcuts on Message triggers). _(Assumed yes.)_
- **R9.** Should iMessages-from-other-Apple-users also be captured? Apple's
  Message trigger fires on both SMS and iMessage, so default is **yes**;
  filter to SMS only if she prefers.
- **R10.** Daily backstop time — 03:00 ET assumed; flip if she leaves the
  phone on Do-Not-Disturb during that window.
- **R11.** Numbers to include — single device (her primary cell) at first;
  multi-device adds a `device_id` column later.

## 10. Implementation plan (under the existing Phase 1 hours)

| # | Task | Est. |
|---|---|---|
| 1 | Add `sms_inbox_raw` migration | 0.5 h |
| 2 | `app/ingestion/sms_phone.py` (webhook + HMAC + idempotent insert) | 2 h |
| 3 | `app/jobs/sms_poll.py` (poller, SKIP LOCKED, dead-lettering) | 2 h |
| 4 | Wire into `app/main.py` lifespan + `app/config.py` env vars | 0.5 h |
| 5 | Dashboard panel | 1 h |
| 6 | iOS Shortcut: design, build, sign, install on Nathalie's phone | 2 h |
| 7 | Tests: webhook auth, idempotency, poller batch, dead-letter | 2 h |
| 8 | Update `09_Advanced_Tests/` with a phone-SMS scenario | 1 h |
| | **Total** | **11 h** |

Fits inside the Phase 1 contract (39.3 h, 26 h already spent).

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
