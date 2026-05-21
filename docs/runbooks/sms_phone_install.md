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

Build once on Tal's iPhone (it's easier to author there than on Nathalie's),
share via iCloud link to Nathalie, walk her through the install on a
screen-share. Requires iOS 15 or later.

### 2.1 Step-by-step build

Open the **Shortcuts** app → **Automation** tab (bottom) → **+** (top right)
→ **Create Personal Automation**.

**Trigger**
1. Scroll down, tap **Message**.
2. Sender: leave as **Any** (specific senders can be added later).
3. **Run Immediately**: toggle **ON** (no confirmation tap on each fire;
   iOS 15+ behavior).
4. Tap **Next**.

**Actions** — add these in order using the **+ Add Action** button.

**Action 1 — Date**
- Search "Date" → tap **Date**.
- Leave as **Current Date**.
- Output variable name (rename for clarity): **Now**.

**Action 2 — Format Date**
- Search "Format Date" → tap **Format Date**.
- Date: tap and pick the **Now** magic variable from Action 1.
- Date Format: tap and choose **Custom**.
- Format String: paste exactly:
  ```
  yyyy-MM-dd'T'HH:mm:ssXXX
  ```
- Output variable name: **NowISO**.

**Action 3 — Dictionary**
- Search "Dictionary" → tap **Dictionary**.
- Tap **+ Add new item** three times. Set each to **Text** type and:
  - Key `from_number`, Value: pick **Magic Variable** → **Sender**
    (from the Message trigger).
  - Key `body`, Value: pick **Magic Variable** → **Message** (from the
    Message trigger).
  - Key `received_at_phone`, Value: pick **Magic Variable** → **NowISO**
    (Action 2).
- Output variable name: **Payload**.

**Action 4 — Get Contents of URL**
- Search "Get Contents of URL" → tap **Get Contents of URL**.
- URL field: paste exactly:
  ```
  https://router.apteker.proactiverev.com/webhooks/sms_phone
  ```
- Tap the expand arrow (▼) to reveal the rest of the action.
- **Method**: tap and change to **POST**.
- **Headers**: tap **Add new header** twice.
  - Key `Authorization`, Text: `Bearer <PASTE PRIMARY TOKEN HERE>`
  - Key `Content-Type`, Text: `application/json`
- **Request Body**: tap and change to **JSON**.
  - Then tap the body field and pick **Magic Variable** → **Payload**
    (Action 3).
- Output variable name: **Response** (we won't use it but it makes
  debugging easier).

**Save**
1. Tap **Next**.
2. Review the summary; tap **Done**.

The automation now appears in the **Automation** tab.

### 2.2 Self-test on Tal's phone first

1. From a separate device, send Tal's phone a test SMS:
   > test from runbook 2.2
2. Top of the screen: a notification "Running your automation…" appears
   briefly.
3. From a laptop:
   ```bash
   curl -H "Authorization: Bearer $ADMIN_BEARER_TOKEN" \
        https://router.apteker.proactiverev.com/admin/sms_inbox_raw/health
   ```
   `handed_off` should be `>= 1`.
4. If it didn't fire: open Shortcuts → Automation → tap the automation,
   confirm **Run Immediately** is on. iOS sometimes resets this on first
   creation; just toggle it again.

### 2.3 Hand-off to Nathalie

1. In Shortcuts on Tal's phone, long-press the automation → **Share**.
2. Choose **iCloud Link** → copy the link.
3. Send it to Nathalie via the same Apple Account email (the link only
   works in Apple's ecosystem).
4. **Before she installs**, edit the shared Shortcut so the `Authorization`
   header uses **her** copy of the primary bearer token. (You can ship
   one Shortcut with a placeholder and have her paste the token once
   during install; whichever is easier on the screen-share.)
5. On the screen-share: tap her iCloud link → **Add Shortcut** → tap
   her installed automation → walk her through the **Run Immediately**
   toggle and confirm it's on.
6. Run the same self-test as 2.2 but targeting her phone.

### 2.4 Known iOS quirks to coach Nathalie through

- **Notification banner on every run.** iOS shows a banner "Running your
  automation". She can hide banners for Shortcuts in **Settings →
  Notifications → Shortcuts**; the automation still runs.
- **Low Power Mode.** Sustained Low Power can defer the automation by
  several minutes. The dashboard L1 panel will show a gap; we'll know.
- **Focus modes.** Driving / Sleep / Do Not Disturb Focus modes do **not**
  block the automation by default, but custom Focus profiles can.
  Verify on her primary Focus before declaring done.

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
