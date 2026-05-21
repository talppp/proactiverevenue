# Deployment

Two free options pre-configured. **Render free** is recommended for first
deploy (auto-deploys on git push once connected). **Fly.io** is an
alternative without cold starts.

> I (Claude) cannot click "Deploy" for you — that requires an account
> with each provider in your name. Everything below is pre-built so the
> one-time setup is short.

## Option A — Render (free tier, recommended)

1. Sign in to <https://dashboard.render.com> (GitHub auth).
2. **New +** → **Blueprint** → connect this repo
   (`talppp/proactiverevenue`).
3. Render reads `render.yaml`. Approve the **`apteker-router-l1`**
   service (skip `apteker-router` and the cron for now — they need the
   L2-L6 modules from the Drive project).
4. Set the three secrets in **Environment** for that service:
   - `SMS_BEARER_TOKEN_PRIMARY` — generate with
     `openssl rand -base64 32 | tr '+/' '-_' | tr -d '='`
   - `SMS_BEARER_TOKEN_SECONDARY` — leave blank initially, set later
     when rotating
   - `ADMIN_BEARER_TOKEN` — another freshly generated token
5. Click **Apply**. Render builds the Docker image and rolls it out
   (~3-5 min first time, ~90 s on subsequent deploys).
6. Note the URL Render gives you (e.g.
   `https://apteker-router-l1.onrender.com`). Test:
   ```bash
   curl https://apteker-router-l1.onrender.com/healthz
   curl -X POST https://apteker-router-l1.onrender.com/webhooks/sms_phone \
     -H "Authorization: Bearer $SMS_BEARER_TOKEN_PRIMARY" \
     -H "Content-Type: application/json" \
     -d '{"from_number":"+14045551234","body":"hello from curl","received_at_phone":"2026-05-21T11:59:50+00:00"}'
   ```

### Free-tier caveats
- Cold start after 15 min idle (~20 s to wake). For the iOS Shortcut
  this means the first SMS after a quiet period may take 20-25 s to
  hand off; subsequent SMS are instant. Upgrade to the **starter**
  $7/mo plan if that matters for Nathalie.
- `DB_URL=memory` means state is lost on restart and on every redeploy.
  Acceptable for the validation phase; for production, attach a free
  Postgres (Neon or Supabase) and set `DB_URL` to its connection
  string, then apply `migrations/001_sms_inbox_raw.sql`.

### Optional — CI-triggered auto-deploy

`.github/workflows/deploy-render.yml` triggers a Render deploy hook
once `tests` is green. To enable:
1. Render dashboard → service → **Settings** → **Deploy Hook** → copy URL.
2. GitHub repo → **Settings** → **Secrets and variables** → **Actions**
   → New secret named `RENDER_DEPLOY_HOOK_URL`.

If the secret isn't set, the workflow no-ops — safe by default.

## Option B — Fly.io (free, no cold starts)

```bash
brew install flyctl
fly auth signup       # or: fly auth login
fly launch --copy-config --name apteker-router-l1 --region iad --no-deploy
fly secrets set \
    SMS_BEARER_TOKEN_PRIMARY="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=')" \
    ADMIN_BEARER_TOKEN="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=')"
fly deploy
fly status
fly open                # opens https://apteker-router-l1.fly.dev
```

Fly's free shared-cpu-1x 256 MB VM has no idle cold start; great for the
iOS Shortcut's tight timing. Memory is small — fine for L1 only, plan
to scale up when L2-L6 are added.

## Adding persistent Postgres (optional)

Either provider works. Both have a free tier:

| Provider | Free tier | Notes |
|---|---|---|
| Neon | 3 GB, autosuspend after 5 min idle | Cleanest UX; just paste the connection string |
| Supabase | 500 MB, no autosuspend | Bigger ecosystem, also gives you a SQL editor and dashboards |

After provisioning:
```bash
# Apply the schema
psql "$DB_URL" -f migrations/001_sms_inbox_raw.sql

# Set DB_URL on the deployed service:
# Render:  dashboard → Environment → DB_URL = postgresql+psycopg2://user:pass@host/db
# Fly:     fly secrets set DB_URL="postgresql+psycopg2://..."
```

Restart the service. `GET /healthz` should now report `"db": "postgres"`.

## Hooking up Nathalie's phone

Once the public URL is live, follow `docs/runbooks/sms_phone_install.md`
§2 to build the iOS Shortcut, pointing it at the deployed URL.
