# Code Reference — every method, function, and argument

**Branch:** `claude/open-nathalie-project-IT0ZA`
**Generated:** 2026-05-30
**Scope:** every Python method/function and PowerShell function in the repo,
with argument names, types, and what each does.

Type legend: `str` = text, `int` = integer, `bool` = boolean (True/False),
`float` = decimal, `datetime` = timestamp, `dict` = key/value map,
`list` = ordered array, `tuple` = fixed-length group, `| None` = optional
(may be null), `Any` = any type, `Protocol` = interface contract.

---

## Table of contents
1. [app/config.py](#appconfigpy) — configuration constants
2. [app/logging_setup.py](#apploggingsetuppy) — logging
3. [app/sms_inbox_store.py](#appsms_inbox_storepy) — L1 staging store
4. [app/ingestion/sms_phone.py](#appingestionsms_phonepy) — L1 webhook
5. [app/admin.py](#appadminpy) — admin endpoints
6. [app/jobs/sms_poll.py](#appjobssms_pollpy) — recovery loop
7. [app/main_l1.py](#appmain_l1py) — deployable L1 entry-point
8. [app/pipeline.py](#apppipelinepy) — L2–L6 orchestration
9. [app/store.py](#appstorepy) — domain store (L2–L6)
10. [app/ws.py](#appwspy) — dashboard WebSocket hub
11. [app/main.py](#appmainpy) — full-pipeline entry-point
12. [scripts/smoke_test.ps1](#scriptssmoke_testps1) — PowerShell functions
13. [scripts/build_ios_shortcut.py](#scriptsbuild_ios_shortcutpy)
14. [scripts/prune_channels*.py](#scriptsprune_channelspy)
15. [HTTP endpoint summary](#http-endpoint-summary)
16. [Environment variables](#environment-variables)

---

## app/config.py

Module-level constants, each read from an environment variable with a
default. No functions — these are values imported elsewhere as
`config.NAME`.

| Constant | Type | Default | Meaning |
|---|---|---|---|
| `PROJECT_ROOT` | `Path` | (computed) | Repo root directory. |
| `DB_URL` | `str` | `"memory"` | `"memory"` = in-process store; a `postgresql+psycopg2://…` URL = real Postgres. |
| `ANTHROPIC_API_KEY` | `str \| None` | unset | Claude API key for L3 classifiers. |
| `LLM_ENABLED` | `bool` | `True` if key set | Whether the LLM fallback is active. |
| `LLM_MODEL` | `str` | `"claude-haiku-4-5-20251001"` | Model id for classification. |
| `LLM_MAX_INPUT_TOKENS` | `int` | `4000` | Per-request input cap. |
| `LLM_MAX_OUTPUT_TOKENS` | `int` | `400` | Per-request output cap. |
| `LLM_DAILY_USD_CEILING` | `float` | `10.0` | Daily LLM spend ceiling. |
| `ROUTING_RULES_PATH` | `Path` | (computed) | Path to `routing_rules.yaml`. |
| `TAXONOMY_PATH` | `Path` | (computed) | Path to `taxonomy.json`. |
| `SCHEMA_SQL_PATH` | `Path` | (computed) | Path to the L2–L6 schema SQL. |
| `CONFIDENCE_FLOOR` | `float` | `0.55` | Below this, route to owner with "unsure" flag. |
| `SMS_POLL_INTERVAL_SECONDS` | `int` | `30` | Recovery-loop tick cadence. |
| `SMS_POLL_BATCH_SIZE` | `int` | `50` | Max rows drained per recovery tick. |
| `SMS_POLL_MAX_ATTEMPTS` | `int` | `5` | Failures before a row → `dead_letter`. |
| `SMS_STUCK_THRESHOLD_SECONDS` | `int` | `30` | Age before a `new` row is "stuck". |
| `SMS_BEARER_TOKEN_PRIMARY` | `str \| None` | unset | Active webhook bearer token. |
| `SMS_BEARER_TOKEN_SECONDARY` | `str \| None` | unset | Second valid token (rotation overlap). |
| `ADMIN_BEARER_TOKEN` | `str \| None` | unset | Token for `/admin/*` endpoints. |
| `ORG_NAME` | `str` | `"Apteker Realty"` | Seed org name. |
| `ORG_TIMEZONE` | `str` | `"America/New_York"` | Seed org timezone. |
| `TEAM_SEED` | `list[tuple]` | (3 members) | Seed team: (name, role, counties, after_hours_ok, max_open). |

---

## app/logging_setup.py

Structured logging via `structlog`.

### `setup(level="INFO")`
Configures process-wide structured logging. Runs once at import.
- `level` (`str`, default `"INFO"`) — log level name (`"DEBUG"`,
  `"INFO"`, `"WARNING"`, `"ERROR"`).
- Returns: `None`.

### `get_logger(layer)`
Returns a logger bound with a `layer` field for grep-able traces.
- `layer` (`str`) — the subsystem name, e.g. `"ingestion.sms_phone"`.
- Returns: a structlog bound logger (`.info()`, `.warning()`, `.error()`
  each take an event `str` plus arbitrary keyword context).

---

## app/sms_inbox_store.py

The L1 staging store. Holds inbound SMS as they move
`new → handed_off` (or `→ dead_letter`).

### `compute_msg_id(from_number, body, received_at_phone)`
Deterministic primary key for a message. Same inputs → same id, so
iPhone retries collide on the PK instead of duplicating.
- `from_number` (`str`) — sender's phone number (E.164).
- `body` (`str`) — the SMS text.
- `received_at_phone` (`datetime`) — phone-side receipt timestamp.
- Returns: `str` — a 64-char SHA-256 hex digest.

### `class SmsRow` (dataclass)
One staging-table row.

| Field | Type | Meaning |
|---|---|---|
| `msg_id` | `str` | Deterministic PK. |
| `from_number` | `str` | Sender E.164. |
| `to_number` | `str \| None` | Recipient (Nathalie's line). |
| `body` | `str` | SMS text. |
| `received_at_phone` | `datetime` | Phone-side timestamp. |
| `received_at` | `datetime` | Server insert time. |
| `status` | `str` | `"new"` / `"handed_off"` / `"dead_letter"`. |
| `attempts` | `int` | Failed handoff count (default 0). |
| `last_error` | `str \| None` | Last failure message. |
| `handed_off_at` | `datetime \| None` | When it reached L2. |

### `class SmsInboxStore` (Protocol)
The interface both store implementations satisfy. Methods below are
shared by `InMemorySmsInboxStore` and `PostgresSmsInboxStore`.

#### `insert_new(from_number, to_number, body, received_at_phone)`
Idempotently insert a row keyed on the computed `msg_id`.
- `from_number` (`str`), `to_number` (`str | None`), `body` (`str`),
  `received_at_phone` (`datetime`).
- Returns: `tuple[SmsRow, bool]` — the row, and `was_new` (`True` if
  freshly inserted, `False` if it already existed = duplicate).

#### `mark_handed_off(msg_id)`
Mark a row successfully delivered to L2 (sets `status='handed_off'`,
stamps `handed_off_at`, clears `last_error`).
- `msg_id` (`str`).
- Returns: `None`.

#### `mark_attempt_failed(msg_id, error, max_attempts)`
Increment the attempt counter; promote to `dead_letter` once the cap is
hit.
- `msg_id` (`str`), `error` (`str`, truncated to 500 chars),
  `max_attempts` (`int`).
- Returns: `str` — the new status.

#### `fetch_stuck(threshold_seconds, batch_size)`
Find `new` rows older than the threshold (for the recovery loop).
- `threshold_seconds` (`int`) — minimum age.
- `batch_size` (`int`) — max rows to return.
- Returns: `list[SmsRow]`, oldest first.

#### `fetch(msg_id)`
- `msg_id` (`str`).
- Returns: `SmsRow | None`.

#### `reset_for_replay(msg_id)`
Resurrect a `dead_letter` row back to `new` (admin replay).
- `msg_id` (`str`).
- Returns: `bool` — `True` if a dead-letter row was reset, else `False`.

#### `counts_by_status()`
- Returns: `dict[str, int]` — counts keyed `new`/`handed_off`/`dead_letter`.

### `class InMemorySmsInboxStore` (dataclass)
Thread-safe dict-backed implementation for tests and the `memory` demo.
Constructor fields:
- `_rows` (`dict[str, SmsRow]`) — the storage.
- `_lock` (`threading.Lock`) — guards all mutations.
- `_now` (`Callable[[], datetime]`, default `_utcnow`) — clock injection
  so tests can simulate row aging.

Implements every Protocol method above against the in-memory dict.

### `class PostgresSmsInboxStore`
SQLAlchemy-backed implementation (production).

#### `__init__(db_url)`
- `db_url` (`str`) — the Postgres connection URL.
- Creates a SQLAlchemy engine with `pool_pre_ping=True` (validate
  connections before use) and `pool_recycle=300` (drop connections older
  than 5 min — survives Supabase pooler recycling).

Implements every Protocol method using parameterized SQL
(`INSERT … ON CONFLICT DO NOTHING RETURNING`, the
`CASE WHEN attempts+1 >= :maxn THEN 'dead_letter'` promotion, etc.).

Internal helpers:
- `_fetch(conn, msg_id)` — `conn` (SQLAlchemy connection), `msg_id`
  (`str`) → `SmsRow | None`. One-row SELECT reusing an open connection.
- `_row_from_db(r)` (static) — `r` (a DB row tuple) → `SmsRow`.

### `make_sms_inbox_store(db_url)`
Factory that picks the backend.
- `db_url` (`str`) — `""` or `"memory"` → `InMemorySmsInboxStore`;
  anything else → `PostgresSmsInboxStore`.
- Returns: a store implementing `SmsInboxStore`.

### `_utcnow()`
- Returns: `datetime` — current UTC time (the default clock).

---

## app/ingestion/sms_phone.py

The L1 webhook router (`POST /webhooks/sms_phone`).

### `class SmsPhonePayload` (Pydantic model)
Validates the inbound JSON body.

| Field | Type | Rules |
|---|---|---|
| `from_number` | `str` | required, 1–64 chars |
| `to_number` | `str \| None` | optional, ≤ 64 chars |
| `body` | `str` | required, 1–4096 chars |
| `received_at_phone` | `datetime \| None` | optional; server stamps now() if omitted |

### `_verify_bearer(authorization=None)`
FastAPI dependency that authenticates the webhook.
- `authorization` (`str | None`) — the `Authorization` header, injected
  by FastAPI.
- Raises `HTTPException` 503 (no tokens configured), 401 (missing/bad
  bearer). Uses constant-time compare against the primary + secondary
  tokens.
- Returns: `None` on success.

### `_pipeline_payload(row)`
Shape a staged row into the dict the L2 pipeline expects.
- `row` (`SmsRow`).
- Returns: `dict[str, Any]` with `msg_id`, `provider_msg_id`,
  `from_number`, `to_number`, `body`, `received_at` (ISO string).

### `async _handoff(store, pipeline, max_attempts, row)`
Hand one row to L2; record success/failure on the store. Runs as a
FastAPI BackgroundTask.
- `store` (`SmsInboxStore`), `pipeline` (`Any` — anything with
  `async handle_inbound(channel, payload)`), `max_attempts` (`int`),
  `row` (`SmsRow`).
- Returns: `None`. On exception or a `{"status":"rejected"}` result,
  calls `mark_attempt_failed`; otherwise `mark_handed_off`.

### `_get_store(request)`
Fetch the per-app staging store, lazily creating an in-memory one if the
lifespan didn't attach one.
- `request` (`fastapi.Request`).
- Returns: `SmsInboxStore`.

### `async receive_sms_phone(payload, background, request, _)`
The webhook handler.
- `payload` (`SmsPhonePayload`) — validated body.
- `background` (`BackgroundTasks`) — FastAPI background queue.
- `request` (`Request`).
- `_` (`None`) — the `_verify_bearer` dependency (auth gate).
- Returns: `dict` — `{"status":"queued","msg_id":<str>,"duplicate":<bool>}`.
  Schedules `_handoff` only when the row is new.

### Module constant
- `CHANNEL` (`str`) = `"sms_phone"` — propagated to L2–L6 routing.

---

## app/admin.py

Operator endpoints, all under `/admin`, all gated by the admin bearer.

### `_verify_admin(authorization=None)`
FastAPI dependency authenticating admin calls.
- `authorization` (`str | None`) — header.
- Raises `HTTPException` 503 (not configured) / 401 (missing/bad bearer).
- Returns: `None`.

### `class InjectPayload` (Pydantic model)
Body for manual backfill. Same fields as `SmsPhonePayload` **except**
`received_at_phone` is **required** (`datetime`).

### `async replay(msg_id, background, request, _)`
Resurrect and re-process a dead-lettered row.
- `msg_id` (`str`, from the URL path).
- `background` (`BackgroundTasks`), `request` (`Request`),
  `_` (`None`, admin auth).
- Returns: `dict` `{"status":"queued","msg_id":…}`. Raises 404 if no
  matching dead-letter row.

### `async inject(payload, background, request, _)`
Manually insert a message Nathalie spotted that never arrived.
- `payload` (`InjectPayload`), `background` (`BackgroundTasks`),
  `request` (`Request`), `_` (`None`, admin auth).
- Returns: `dict` `{"status":"queued","msg_id":…,"duplicate":<bool>}`.

### `async health(request, _)`
Status counts for the dashboard.
- `request` (`Request`), `_` (`None`, admin auth).
- Returns: `dict[str, int]` — counts by status.

---

## app/jobs/sms_poll.py

### `async drain_stuck(store, pipeline)`
One pass of the recovery loop: drain `new` rows stuck past the
threshold, retrying each through L2; dead-letter after the attempt cap.
- `store` (`SmsInboxStore`).
- `pipeline` (`Any` with `async handle_inbound`).
- Returns: `int` — number of rows successfully handed off this tick.
  Per-row exceptions are caught and recorded; they don't abort the batch.
  Reads `SMS_STUCK_THRESHOLD_SECONDS`, `SMS_POLL_BATCH_SIZE`,
  `SMS_POLL_MAX_ATTEMPTS` from config.

---

## app/main_l1.py

The standalone, deployable L1 service (`uvicorn app.main_l1:app`).

### `class LoggingPipeline`
A stub L2 used until the real pipeline is wired. Records inbounds and
returns the canonical success shape.
- `__init__()` — sets `self.received` (`list[tuple[str, dict]]`).
- `async handle_inbound(channel, payload)` — `channel` (`str`),
  `payload` (`dict[str, Any]`). Returns `dict`
  `{"status":"ok","message_id":…,"deliveries":[]}`.

### `async _sms_poll_loop(app, interval)`
Background task that calls `drain_stuck` forever, sleeping between ticks.
- `app` (`FastAPI`) — to read `app.state`.
- `interval` (`float`) — seconds between ticks.
- Returns: never (runs until cancelled). Catches and one-line-logs DB
  errors so an outage doesn't flood logs or crash the loop.

### `async lifespan(app)`
Startup/shutdown context manager.
- `app` (`FastAPI`).
- On startup: builds the store from `DB_URL`, attaches a
  `LoggingPipeline`, launches the poll loop. On shutdown: cancels the
  loop. Yields once.

### `async healthz()`
Liveness + DB reachability. **Always returns HTTP 200** so a transient
DB outage doesn't make Render tear down the deploy.
- No arguments.
- Returns: `dict` — `ok` (`bool`), `layer` (`str`), `db`
  (`"memory"`/`"postgres"`/`"error"`), `counts` (`dict`), and `error`
  (`str`) when degraded.

### `async root()`
- No arguments.
- Returns: `HTMLResponse` — a tiny landing page.

---

## app/pipeline.py

L2–L6 orchestration (lives in the Drive project; the repo has the
interface this L1 calls).

### `class Pipeline`
#### `__init__(store)`
- `store` (`Store`) — the domain store. Loads routing rules once.

#### `async handle_inbound(channel, payload)`
The public entry-point L1 calls. Runs normalize → classify (urgency,
topic, geo) → route → assign → deliver, writing audit rows throughout.
- `channel` (`str`) — e.g. `"sms_phone"`.
- `payload` (`dict[str, Any]`) — channel-specific message fields.
- Returns: `dict` — summary with `status`, `message_id`, `urgency`,
  `topic`, `owner`, `deliveries`, etc. (or `{"status":"duplicate"}` /
  `{"status":"rejected"}`).

#### `_contact_is_vip(sender_handle)`
- `sender_handle` (`str`) — sender identifier.
- Returns: `bool` — whether the contact is flagged VIP.

---

## app/store.py

Domain persistence for L2–L6. Two backends behind a `Store` base class.

### Dataclasses (records)
- `InboundMessage` — fields: `id`,`org_id`,`channel`,`provider_msg_id`,
  `sender_handle`,`subject`,`body`,`body_redacted`,`language`,
  `received_at`,`dedupe_hash` (all `str`/`datetime`), `raw_payload`
  (`dict`).
- `Classification` — `id`,`message_id`,`model_name`,`urgency`,`topic`
  (`str`); `urgency_confidence`,`topic_confidence`,`cost_usd` (`float`);
  `is_business`,`is_follow_up` (`bool`); `latency_ms` (`int`); plus
  optional detail fields.
- `RoutingDecision` — `id`,`message_id`,`classification_id`,
  `primary_owner`,`fallback_owner`,`rule_id_matched`,`reason` (`str`);
  `is_archive`,`is_unsure` (`bool`).
- `DeliveryEvent` — `id`,`routing_id`,`delivery_kind`,`target_handle`,
  `status`,`idempotency_key` (`str`); `sent_at` (`datetime`); `error`
  (`str | None`).

### `class Store` (base — raises NotImplementedError)
Interface methods: `init_schema()`, `seed()`, `insert_message(m)`,
`insert_classification(c)`, `insert_routing(r)`, `insert_delivery(d)`,
`insert_audit(**kw)`, `existing_dedupe(dedupe_hash: str) -> bool`,
`existing_idempotency(key: str) -> bool`,
`team_member(name: str) -> dict | None`.

### `class InMemoryStore(Store)` / `class PostgresStore(Store)`
Concrete implementations. `PostgresStore.__init__(db_url: str)` opens a
SQLAlchemy engine; `_channel_account(channel: str) -> str` and
`_team_id(name: str) -> str | None` are internal lookups.

### `make_store()`
- No arguments (reads `config.DB_URL`).
- Returns: `Store` — in-memory or Postgres.

### `sha1_dedupe(channel, sender, body)`
Dedupe hash for the domain layer.
- `channel` (`str`), `sender` (`str`), `body` (`str`).
- Returns: `str` — SHA-1 hex of `channel|sender|body[:200]`.

### `_utcnow()` → `datetime`.

---

## app/ws.py

In-process WebSocket hub for the team dashboards.

### `class Hub`
- `__init__()` — initializes the member map + async lock.
- `_get(name)` — `name` (`str`) → `_MemberHub` (creates on first use).
- `async connect(name, ws)` — `name` (`str`), `ws` (`WebSocket`).
  Accepts the socket, registers it, replays the buffered cards.
  Returns `None`.
- `async disconnect(name, ws)` — same args; deregisters. Returns `None`.
- `async publish(name, card)` — `name` (`str`), `card` (`dict[str,Any]`).
  Buffers + broadcasts to all connected clients. Returns `int` — number
  of clients that received it.
- `async serve(name, ws)` — `name` (`str`), `ws` (`WebSocket`). Connect
  + idle until disconnect. Returns `None`.

### `class _MemberHub` (dataclass)
- `name` (`str`), `clients` (`set[WebSocket]`),
  `buffer` (`deque[dict]`, last 50 cards).

### Module singleton
- `HUB` (`Hub`) — process-wide instance.

---

## app/main.py

The full-pipeline FastAPI app (L1→L6). Routes (all `async`):

| Route | Handler | Args | Returns |
|---|---|---|---|
| `GET /healthz` | `healthz()` | none | `dict` — liveness + rules info |
| `GET /metrics` | `metrics()` | none | `dict` — in-process counters |
| `GET /` | `root()` | none | `HTMLResponse` |
| `GET /inbox/{member}` | `inbox(member)` | `member` (`str`) | `HTMLResponse` (dashboard) |
| `WS /ws/{member}` | `ws_subscribe(websocket, member)` | `websocket` (`WebSocket`), `member` (`str`) | streams cards |
| `POST /api/v1/handle/{message_id}` | `mark_handled(message_id)` | `message_id` (`str`) | `204` |

Internal: `_sms_poll_loop(app, interval)` (same as main_l1),
`lifespan(app)`, `_unhandled(request, exc)` (catch-all 500 handler —
`request` (`Request`), `exc` (`Exception`)).

---

## scripts/smoke_test.ps1

PowerShell smoke test for the live deployment.

### Parameters (`param(...)` block)
- `-Url` (`string`, default the Render URL) — base URL to test.
- `-SmsToken` (`string`) — the `SMS_BEARER_TOKEN_PRIMARY`.
- `-AdminToken` (`string`) — the `ADMIN_BEARER_TOKEN`.

### `function Show-Json`
- `-Object` (any) — an object to pretty-print as JSON.
- Output: writes indented JSON to the console.

### `function Record`
Record + print one check's result.
- `-Name` (`string`) — the check's label.
- `-Pass` (`bool`) — whether it passed.
- `-Detail` (`string`, optional) — extra context.
- Output: prints `PASS`/`FAIL` (colored) and appends to `$script:results`.

### `function Invoke-Api`
HTTP wrapper that never throws — normalizes success and error into one
shape.
- `-Method` (`string`, default `"GET"`) — HTTP verb.
- `-Path` (`string`) — path appended to `$Url`.
- `-Headers` (`hashtable`, default empty) — request headers.
- `-Body` (any, optional) — JSON body for POSTs.
- Returns: a hashtable `@{ StatusCode = <int>; Body = <object|string>;
  Error = <string|null> }`.

The rest of the script is procedural: it generates a per-run id +
timestamp, then runs 9 checks (healthz, no-bearer 401, wrong-bearer 401,
happy path, idempotency, malformed 422, admin no-bearer 401, admin
health, admin inject) and exits `0` if all pass, `1` otherwise.

---

## scripts/build_ios_shortcut.py

Generates an unsigned `.shortcut` plist (Mac-signing or pre-iOS-26 only).

### `build_shortcut(url, bearer_token)`
Builds the full shortcut action graph.
- `url` (`str`) — base service URL.
- `bearer_token` (`str`) — token to embed in the Authorization header.
- Returns: `dict` — the plist structure ready to serialize.

### `main()`
- No arguments (parses `--url`, `--token`, `--out` from the CLI).
- Returns: `int` — process exit code.

### Internal builders (all return `dict`)
- `_uuid()` → `str` — uppercase UUID.
- `_magic_var(token_string, attachments)` — `token_string` (`str`),
  `attachments` (`dict`).
- `_attachment(action_uuid, output_name="Dictionary")` —
  `action_uuid` (`str`), `output_name` (`str`).
- `_attachment_input()`, `_attachment_date_current()` — no args.

---

## scripts/prune_channels*.py

Three-stage tooling that removed Gmail + Instagram from the project.

### prune_channels.py (stage 1 — generic regex pass)
- `should_skip(path, root)` — `path`/`root` (`Path`) → `bool` (skip
  vendored/cache dirs).
- `edit_text(path, text)` — `path` (`Path`), `text` (`str`) →
  `tuple[str, list[str]]` (new text, notes).
- `find_prose_mentions(path, text)` — `path` (`Path`), `text` (`str`) →
  `list[str]` (lines to review).
- `regen_doc_sources(root)` — `root` (`Path`) → `None` (re-runs the
  `build_*.py` doc generators).
- `main()` → `int` (CLI: `--root`, `--apply`, `--regen`).

### prune_channels_phase2.py (stage 2 — surgical per-file edits)
- `main()` → `int` (CLI: `--root`, `--apply`). Applies a dict of
  per-file `(regex, replacement)` pairs.

### prune_channels_phase3.py (stage 3 — restore + line-based clean)
- `_strip_function_def(text)` / `_strip_route(text)` /
  `_strip_quoted_lines(text)` / `_strip_url_paths(text)` /
  `_strip_imports(text)` / `_strip_tuple_box(text)` /
  `_readme_fix(text)` — each takes `text` (`str`), returns
  `tuple[str, int]` (new text, count removed).
- `clean(text, *, is_readme=False, is_diagram=False)` — `text` (`str`),
  `is_readme` (`bool`), `is_diagram` (`bool`) → `tuple[str, dict]`.
- `main()` → `int` (CLI: `--root`, `--backup`, `--apply`). Restores the
  3 files broken by stage 1, re-cleans them, then `ast.parse`-checks.

---

## HTTP endpoint summary

| Method | Path | Auth | Body | Success response |
|---|---|---|---|---|
| `POST` | `/webhooks/sms_phone` | `Bearer SMS_BEARER_TOKEN_*` | `SmsPhonePayload` | `200 {status, msg_id, duplicate}` |
| `POST` | `/admin/sms_inbox_raw/{msg_id}/replay` | `Bearer ADMIN_BEARER_TOKEN` | none | `200 {status, msg_id}` |
| `POST` | `/admin/sms_inbox_raw/inject` | `Bearer ADMIN_BEARER_TOKEN` | `InjectPayload` | `200 {status, msg_id, duplicate}` |
| `GET` | `/admin/sms_inbox_raw/health` | `Bearer ADMIN_BEARER_TOKEN` | none | `200 {new, handed_off, dead_letter}` |
| `GET` | `/healthz` | none | none | `200 {ok, layer, db, counts}` |
| `GET` | `/` | none | none | `200` HTML |

Status codes: `401` bad/missing bearer, `422` invalid body, `404`
replay of a non-dead-letter id, `503` server not configured (no tokens).

---

## Environment variables

| Var | Type | Default | Used by |
|---|---|---|---|
| `DB_URL` | `str` | `memory` | store factory |
| `SMS_BEARER_TOKEN_PRIMARY` | `str` | unset (required in prod) | webhook auth |
| `SMS_BEARER_TOKEN_SECONDARY` | `str` | unset | webhook auth (rotation) |
| `ADMIN_BEARER_TOKEN` | `str` | unset (required in prod) | admin auth |
| `SMS_POLL_INTERVAL_SECONDS` | `int` | `30` | recovery loop |
| `SMS_POLL_BATCH_SIZE` | `int` | `50` | recovery loop |
| `SMS_POLL_MAX_ATTEMPTS` | `int` | `5` | dead-lettering |
| `SMS_STUCK_THRESHOLD_SECONDS` | `int` | `30` | stuck detection |
| `LOG_LEVEL` | `str` | `INFO` | logging |
| `ANTHROPIC_API_KEY` | `str` | unset | L3 classifiers (full pipeline) |
| `PORT` | `int` | `8000` | Docker/uvicorn bind |
