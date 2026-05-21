# SMS-phone ingestion — install & token-rotation runbook

## 0. Wiring into the existing project

Three small edits to land this feature in `app/main.py`:

```python
# imports
from app import config
from app.admin import router as admin_router
from app.ingestion.sms_phone import router as sms_phone_router
from app.jobs.sms_poll import drain_stuck
from app.sms_inbox_store import InMemorySmsInboxStore

# inside lifespan(), after store/pipeline are set on app.state:
app.state.sms_inbox_store = InMemorySmsInboxStore()  # swap for Postgres impl in prod

# alongside the existing escalation_task:
async def _sms_poll_loop():
    import asyncio
    while True:
        try:
            await drain_stuck(app.state.sms_inbox_store, app.state.pipeline)
        except Exception as e:
            log.exception("sms_poll_iteration_failed", error=str(e))
        await asyncio.sleep(config.SMS_POLL_INTERVAL_SECONDS)
sms_poll_task = asyncio.create_task(_sms_poll_loop())
app.state.sms_poll_task = sms_poll_task

# at module scope, after the existing inbound_router include:
app.include_router(sms_phone_router)
app.include_router(admin_router)
```

In `app/ingestion/normalize.py`, add `'sms_phone'` to the channel
dispatch (treat its payload the same as `'sms'` — fields are identical
except for the timestamp source).

Apply the migration: `psql $DB_URL -f migrations/001_sms_inbox_raw.sql`.



Audience: ops (Tal). Nathalie only sees the Shortcut install step.

## 1. Provision tokens

```bash
# 32 bytes of base64url, no padding
openssl rand -base64 32 | tr '+/' '-_' | tr -d '='
```

Set these on Render → service env vars:

| Var | When |
|---|---|
| `SMS_BEARER_TOKEN_PRIMARY` | always; what the active Shortcut presents |
| `SMS_BEARER_TOKEN_SECONDARY` | during rotation overlap only |
| `ADMIN_BEARER_TOKEN` | always; used by `/admin/*` |
| `SMS_POLL_INTERVAL_SECONDS` | default 30 — sweep cadence for stuck rows |
| `SMS_STUCK_THRESHOLD_SECONDS` | default 30 — how long a row may sit at 'new' before the recovery loop picks it up |
| `SMS_POLL_BATCH_SIZE` | default 50 |
| `SMS_POLL_MAX_ATTEMPTS` | default 5 — after this many failures, row → 'dead_letter' |

Never commit tokens to git.

## 2. iOS Shortcut

Build once in the Shortcuts app on Tal's iPhone (it's easier to author there
than on Nathalie's), AirDrop the signed shortcut to Nathalie, walk her through
the install on a screen-share.

### Automation
1. Shortcuts → Automations → New → Personal Automation → **Message** trigger
2. Sender: **Any**; Run Immediately: **ON**
3. Add action: **Get Contents of URL**
   - URL: `https://router.apteker.proactiverev.com/webhooks/sms_phone`
   - Method: **POST**
   - Headers:
     - `Authorization: Bearer <SMS_BEARER_TOKEN_PRIMARY>`
     - `Content-Type: application/json`
   - Request Body: **JSON**
     ```json
     {
       "from_number":       "<Magic Variable: Sender>",
       "body":              "<Magic Variable: Message>",
       "received_at_phone": "<Magic Variable: Current Date (formatted ISO 8601)>"
     }
     ```

### Verification before handing back to Nathalie
- From any other phone, text Nathalie's line: "test from runbook step 2.4".
- `curl -H "Authorization: Bearer $ADMIN_BEARER_TOKEN"
  https://.../admin/sms_inbox_raw/health` should show `handed_off >= 1`.

## 3. Zero-downtime token rotation

1. Generate a new token; set `SMS_BEARER_TOKEN_SECONDARY` to the **old**
   primary, set `SMS_BEARER_TOKEN_PRIMARY` to the **new** value.
2. Rebuild and deliver the Shortcut to Nathalie's phone with the new token.
3. After 24 h of clean traffic, clear `SMS_BEARER_TOKEN_SECONDARY`.

The webhook accepts either token while both are set, so the rotation has no
window where her phone fails to post.

## 4. Operating

- **Dashboard L1 panel** shows `new / handed_off (last 1h) / dead_letter`
  counts.
- A non-zero `new` count > 30 s old means the recovery loop is doing work —
  fine, but watch the trend.
- Sustained `dead_letter` growth = look at `last_error` on each row and fix
  the L2 issue. Replay via:
  ```bash
  curl -X POST -H "Authorization: Bearer $ADMIN_BEARER_TOKEN" \
       https://.../admin/sms_inbox_raw/<msg_id>/replay
  ```
- If Nathalie says "you missed this one":
  ```bash
  curl -X POST -H "Authorization: Bearer $ADMIN_BEARER_TOKEN" \
       -H "Content-Type: application/json" \
       -d '{"from_number":"+1...","body":"...","received_at_phone":"2026-05-21T13:42:00-04:00"}' \
       https://.../admin/sms_inbox_raw/inject
  ```

## 5. Known gaps

- iOS may defer the Personal Automation under sustained Low Power Mode or
  when the phone is hard-off — accepted 2–5% miss rate per the design
  (ADR-001 captures the Mac-agent option to close it).
- The Shortcut runs on Nathalie's device only — if she gets a new phone, the
  Shortcut must be re-installed and re-paired with the current bearer token.
