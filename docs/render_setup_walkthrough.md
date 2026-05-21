# Render setup — click-by-click

A first deploy takes ~5 minutes of clicks. Follow these in order.

## Prerequisites
- A GitHub account that can see `talppp/proactiverevenue`.
- A web browser. Nothing else.

## 1. Create the Render account (skip if you already have one)
1. Open <https://render.com> → **Get Started** (top right).
2. **Sign in with GitHub** — easiest because Render needs repo access
   anyway. Authorize the OAuth prompt.
3. You land on the Render dashboard. Skip any onboarding wizard
   ("What are you building?") — pick **I'll explore on my own**.

## 2. Connect the repo to Render
1. Top right: **New +** → **Blueprint**.
2. **Connect GitHub** (if not already connected). Authorize the Render
   GitHub App. When it asks which repos to grant access to, you can
   choose **Only select repositories** and pick just
   `talppp/proactiverevenue`. Cleaner than granting access to all.
3. After the GitHub App finishes installing, you'll see a list of your
   repos. Click `proactiverevenue`.

## 3. Apply the blueprint
1. Render reads `render.yaml` from your branch. The branch picker is
   at the top — make sure it's set to **`claude/open-nathalie-project-IT0ZA`**
   (or whatever branch we're shipping from).
2. Render shows a preview: **2 services + 1 cron** will be created.
   - `apteker-router-l1` — **the one we want.** Free tier, Docker.
   - `apteker-router` — full pipeline. Has `autoDeploy: false` so it
     won't actually deploy. Safe to leave declared.
   - `apteker-router-p0-escalate` — cron for the full pipeline. Same
     deal, won't run on its own.
3. Give the blueprint a name (e.g. `apteker-l1`). Click **Apply**.

## 4. Wait for the first build
1. Render starts building the Docker image. Watch the Logs tab on the
   `apteker-router-l1` service.
2. First build takes ~3-5 min (it pulls the `python:3.11-slim` base,
   installs deps from `requirements.txt`, copies app source). Subsequent
   builds take ~90 s thanks to layer caching.
3. The build is done when you see:
   ```
   ==> Your service is live at https://apteker-router-l1-xxxx.onrender.com
   ```
   Copy that URL.

## 5. Set the secrets
The blueprint declared three env vars with `sync: false`, meaning they
must be set in the dashboard, not in `render.yaml`. Render flagged them.

1. On the `apteker-router-l1` service page, click **Environment**.
2. You'll see three rows missing values:
   - `SMS_BEARER_TOKEN_PRIMARY`
   - `SMS_BEARER_TOKEN_SECONDARY`
   - `ADMIN_BEARER_TOKEN`
3. Generate two fresh tokens locally — open a terminal:
   ```bash
   openssl rand -base64 32 | tr '+/' '-_' | tr -d '='
   ```
   Run twice. Two distinct base64 strings.
4. Paste the **first** value into `SMS_BEARER_TOKEN_PRIMARY`.
5. Paste the **second** into `ADMIN_BEARER_TOKEN`.
6. Leave `SMS_BEARER_TOKEN_SECONDARY` **blank** for now (you'll fill it
   later when you rotate the primary).
7. Click **Save Changes**. Render redeploys automatically with the new
   env vars (~30 s).

**Write the two tokens down in 1Password / your secret manager NOW.**
You'll need them when building the iOS Shortcut. Render won't show them
again after you navigate away.

## 6. Smoke test from your laptop

Open a terminal:

```bash
URL="https://apteker-router-l1-xxxx.onrender.com"
SMS_TOKEN="<the primary token you saved>"
ADMIN_TOKEN="<the admin token>"

# 1. Health check (no auth required for /healthz on this service)
curl -s "$URL/healthz" | jq .
# Expect: { "ok": true, "layer": "l1_sms_phone", "db": "memory",
#           "counts": { "new": 0, "handed_off": 0, "dead_letter": 0 } }

# 2. Auth — reject missing bearer
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$URL/webhooks/sms_phone" \
    -H "Content-Type: application/json" -d '{}'
# Expect: 401

# 3. Auth — reject wrong bearer
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$URL/webhooks/sms_phone" \
    -H "Authorization: Bearer not-the-real-token" \
    -H "Content-Type: application/json" -d '{}'
# Expect: 401

# 4. Happy path
curl -s -X POST "$URL/webhooks/sms_phone" \
    -H "Authorization: Bearer $SMS_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"from_number":"+14045551234","body":"smoke test from laptop","received_at_phone":"2026-05-21T11:59:50+00:00"}' \
    | jq .
# Expect: { "status": "queued", "msg_id": "<64-hex>", "duplicate": false }

# 5. Idempotency — same payload again
curl -s -X POST "$URL/webhooks/sms_phone" \
    -H "Authorization: Bearer $SMS_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"from_number":"+14045551234","body":"smoke test from laptop","received_at_phone":"2026-05-21T11:59:50+00:00"}' \
    | jq .
# Expect: { "status": "queued", "msg_id": "<same hex>", "duplicate": true }

# 6. Admin health endpoint
curl -s "$URL/admin/sms_inbox_raw/health" \
    -H "Authorization: Bearer $ADMIN_TOKEN" | jq .
# Expect: { "new": 0, "handed_off": 1, "dead_letter": 0 }
#         (1 because step 5 was idempotent — the underlying row was
#         already processed in step 4 and is in handed_off state)
```

If all six work, the deploy is correct.

## 7. (Optional) Wire up CI-gated auto-deploy

By default Render auto-deploys on every push (because `autoDeploy: true`
in the blueprint). That's fine for now, but for stricter discipline we
can change it to "deploy only after CI passes":

1. Render dashboard → `apteker-router-l1` → **Settings** → scroll to
   **Auto-Deploy** → switch to **Off**.
2. Same Settings page → scroll to **Deploy Hook** → click **Copy**.
3. GitHub: <https://github.com/talppp/proactiverevenue/settings/secrets/actions>
   → **New repository secret** → name `RENDER_DEPLOY_HOOK_URL`,
   value = the URL you copied.
4. From now on, every push to `claude/open-nathalie-project-IT0ZA` (or
   `main`) triggers GitHub Actions; if all jobs go green, the
   `deploy-render.yml` workflow fires the hook and Render redeploys.
   If any test fails, the deploy is skipped.

You can verify by pushing a deliberately broken test — CI goes red,
Render does nothing.

## 8. Hook up Nathalie's phone

Follow `docs/runbooks/sms_phone_install.md` §2 to build the iOS Shortcut,
using:
- URL: the Render URL from step 4
- Bearer token in the `Authorization` header: the `SMS_BEARER_TOKEN_PRIMARY`
  value from step 5

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Build fails with `pip install` error | requirements pin drifted | Open the build logs, identify the failing package, pin in `requirements.txt`, push |
| `/healthz` returns 503 immediately | service hasn't finished startup | wait 30 s |
| `/healthz` returns 503 persistently | `lifespan` crashed; check logs | most likely a missing env var or DB URL issue |
| Webhook returns 503 "server not configured" | `SMS_BEARER_TOKEN_PRIMARY` not set in Environment | set it; Render redeploys automatically |
| First message after idle takes 20 s | free-tier cold start | upgrade to starter ($7/mo) or move to Fly (no cold starts) |
| iOS Shortcut returns 401 every time | token mismatch | re-paste the bearer in the Shortcut; whitespace is a common culprit |

## Future upgrades

- **Persistent Postgres.** Sign up for Neon (<https://neon.tech>) or
  Supabase, provision a free DB, run
  `psql "$DB_URL" -f migrations/001_sms_inbox_raw.sql`, then in Render
  Environment set `DB_URL` to the connection string. Restart — `/healthz`
  now reports `"db": "postgres"` and state survives restarts.
- **Upgrade plan.** When you graduate from validation to production,
  switch `plan: free` to `plan: starter` in `render.yaml` and push.
  Render redeploys with no cold starts.
- **Full pipeline.** Once L2-L6 are copied into this repo (currently
  Drive-only), flip `apteker-router`'s `autoDeploy` to `true` and
  the same blueprint deploys the whole thing.
