# Prerequisites for development

What you need to run the L1 SMS-phone service locally, the test suite,
and a cloud deploy.

## Local development

| Item | Version | How to get it |
|---|---|---|
| Python | 3.11 or 3.12 | `brew install python@3.11` / pyenv / asdf |
| pip | shipped with Python | — |
| git | any | — |
| Docker (optional) | any | needed only if you want to build the deploy image locally |
| Postgres (optional) | 14+ | needed only to run the Postgres contract tests; in-memory tests cover the same surface |

```bash
# clone + setup
git clone https://github.com/talppp/proactiverevenue.git
cd proactiverevenue
git checkout claude/open-nathalie-project-IT0ZA
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# run the L1 service locally
export SMS_BEARER_TOKEN_PRIMARY=dev-token
export ADMIN_BEARER_TOKEN=dev-admin
uvicorn app.main_l1:app --reload --port 8000

# in another terminal:
curl -X POST http://localhost:8000/webhooks/sms_phone \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json" \
  -d '{"from_number":"+14045551234","body":"hi","received_at_phone":"2026-05-21T11:59:50+00:00"}'
# {"status":"queued","msg_id":"...","duplicate":false}

curl -H "Authorization: Bearer dev-admin" http://localhost:8000/healthz
```

## Tests

```bash
# unit suite (fast, ~0.3s)
pytest tests -v --ignore=tests/stress

# stress tests (~1-2s)
pytest tests/stress -v

# everything
pytest tests -v
```

Postgres contract tests run only if `TEST_POSTGRES_URL` is set. To run
them locally:

```bash
docker run --rm -d --name pg -p 5432:5432 \
  -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=test \
  postgres:16-alpine
sleep 3
psql postgresql://test:test@localhost:5432/test -f migrations/001_sms_inbox_raw.sql
TEST_POSTGRES_URL=postgresql+psycopg2://test:test@localhost:5432/test \
  pip install psycopg2-binary && \
  pytest tests/test_sms_inbox_store_contract.py -v
docker stop pg
```

## Environment variables

| Var | Required | Default | What it does |
|---|---|---|---|
| `SMS_BEARER_TOKEN_PRIMARY` | yes (prod) | unset | iOS Shortcut posts this in `Authorization: Bearer …` |
| `SMS_BEARER_TOKEN_SECONDARY` | no | unset | second valid token for zero-downtime rotation |
| `ADMIN_BEARER_TOKEN` | yes (prod) | unset | required for `/admin/*` |
| `DB_URL` | no | `memory` | `memory` for in-process; `postgresql+psycopg2://…` for Postgres |
| `SMS_POLL_INTERVAL_SECONDS` | no | `30` | recovery-loop cadence |
| `SMS_POLL_BATCH_SIZE` | no | `50` | rows per tick |
| `SMS_POLL_MAX_ATTEMPTS` | no | `5` | retries before dead-letter |
| `SMS_STUCK_THRESHOLD_SECONDS` | no | `30` | age before recovery loop considers a row stuck |
| `LOG_LEVEL` | no | `INFO` | structlog level |

Use `openssl rand -base64 32 | tr '+/' '-_' | tr -d '='` to generate tokens.

## Cloud deploy

See `docs/deployment.md`.
