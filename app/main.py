"""
FastAPI app entry-point.

  GET  /healthz                     -> liveness check + rules version
  POST /webhook/{channel}           -> L1 routers (see app/ingestion/inbound.py)
  GET  /inbox/{member}              -> dashboard HTML for one team member
  WS   /ws/{member}                 -> WebSocket subscription for cards
  POST /api/v1/handle/{message_id}  -> mark a card 'handled' (writes audit_event)

Run locally:
    uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import HTMLResponse

from app.config import DB_URL, PROJECT_ROOT
from app.ingestion.inbound import router as inbound_router
from app.jobs.p0_escalate import loop as p0_escalate_loop
from app.logging_setup import get_logger
from app.pipeline import Pipeline
from app.router.rules import rules
from app.store import make_store
from app.ws import HUB
from scripts.seed_database import seed_inmemory_store, seed_postgres_store  # noqa: E402

log = get_logger("app")

# ---------------------------------------------------------------------------
# Sentry - exception capture in prod.  Free tier (5K events/mo) is plenty.
# Skipped when SENTRY_DSN is empty so dev/test runs stay quiet.
# ---------------------------------------------------------------------------
_SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if _SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration

        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            integrations=[FastApiIntegration()],
            traces_sample_rate=0.0,                 # transactions off; errors only
            environment=os.environ.get("ENV", "dev"),
            release=os.environ.get("GIT_SHA", "dev"),
        )
        log.info("sentry_initialised")
    except Exception as e:                          # pragma: no cover
        log.warning("sentry_init_failed", error=str(e))

_DASHBOARD_HTML = (PROJECT_ROOT / "dashboard" / "inbox.html").read_text(encoding="utf-8")
_VALID_MEMBERS = {"nathalie", "fabi", "noa"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = make_store()
    if DB_URL == "memory":
        seed_inmemory_store(store)
    else:
        try:
            seed_postgres_store(store)
        except Exception as e:
            # If the schema already exists, just seed the data.
            if "already exists" in str(e):
                store.seed()
            else:
                raise
    app.state.store = store
    app.state.pipeline = Pipeline(store)

    # Background P0 escalation sweep (in-process, every 60s).
    # In prod on Render the same module is run as a separate cron job;
    # here we run it embedded so the demo Just Works.
    escalation_task = asyncio.create_task(p0_escalate_loop(store, interval=60.0))
    app.state.escalation_task = escalation_task

    log.info("app_startup", db_url=DB_URL[:30], rules_version=1,
             escalation_loop="on", sentry=bool(_SENTRY_DSN))
    yield
    escalation_task.cancel()
    try:
        await escalation_task
    except (asyncio.CancelledError, Exception):
        pass
    log.info("app_shutdown")


app = FastAPI(title="Apteker AI Issue Router", version="0.1.0", lifespan=lifespan)
app.include_router(inbound_router)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """Catch-all that logs + (via Sentry SDK) captures, then returns 500."""
    log.exception("unhandled_exception",
                  path=request.url.path, error=str(exc))
    # Sentry FastApiIntegration captures automatically.
    return Response(content='{"error":"internal"}',
                    media_type="application/json", status_code=500)


@app.get("/healthz")
async def healthz():
    return {
        "ok":             True,
        "rules_loaded":   bool(rules()),
        "topic_count":    len(rules().get("topic_routing", [])),
        "db":             "memory" if DB_URL == "memory" else "postgres",
        "sentry":         bool(_SENTRY_DSN),
        "escalation":     "on",
    }


@app.get("/metrics")
async def metrics():
    """Lightweight in-process counters - good enough for Render uptime check."""
    store = app.state.store
    msgs = getattr(store, "messages", {})
    classifications = getattr(store, "classifications", {})
    routings = getattr(store, "routings", {})
    deliveries = getattr(store, "deliveries", {})

    by_urgency: dict[str, int] = {}
    by_topic: dict[str, int] = {}
    by_owner: dict[str, int] = {}
    for c in classifications.values():
        by_urgency[c.urgency] = by_urgency.get(c.urgency, 0) + 1
        by_topic[c.topic] = by_topic.get(c.topic, 0) + 1
    for r in routings.values():
        owner = r.primary_owner or "ARCHIVE"
        by_owner[owner] = by_owner.get(owner, 0) + 1

    return {
        "messages":        len(msgs),
        "classifications": len(classifications),
        "routings":        len(routings),
        "deliveries":      len(deliveries),
        "by_urgency":      by_urgency,
        "by_topic":        by_topic,
        "by_owner":        by_owner,
    }


@app.get("/")
async def root():
    return HTMLResponse(
        "<h1>Apteker AI Issue Router</h1>"
        "<p>Health: <a href='/healthz'>/healthz</a></p>"
        "<p>Inboxes: "
        "<a href='/inbox/nathalie'>/inbox/nathalie</a> · "
        "<a href='/inbox/fabi'>/inbox/fabi</a> · "
        "<a href='/inbox/noa'>/inbox/noa</a></p>"
    )


@app.get("/inbox/{member}", response_class=HTMLResponse)
async def inbox(member: str):
    key = member.lower()
    if key not in _VALID_MEMBERS:
        raise HTTPException(status_code=404, detail="unknown member")
    html = (_DASHBOARD_HTML
            .replace("{{MEMBER}}", key)
            .replace("{{MEMBER_DISPLAY}}", key.capitalize()))
    return HTMLResponse(html)


@app.websocket("/ws/{member}")
async def ws_subscribe(websocket: WebSocket, member: str):
    key = member.lower()
    if key not in _VALID_MEMBERS:
        await websocket.close(code=1008)
        return
    await HUB.serve(key, websocket)


@app.post("/api/v1/handle/{message_id}")
async def mark_handled(message_id: str):
    store = app.state.store
    store.insert_audit(
        actor="member", action="dashboard.marked_handled",
        entity_kind="inbound_message", entity_id=message_id,
        payload={},
    )
    return Response(status_code=204)
