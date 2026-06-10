"""Standalone L1 entry-point — deployable on its own.

The full project's `app.main` imports L2-L6 modules that live in the
Phase 1 Drive project; they aren't in this repo, so `app.main` won't
start in cloud.

This module exposes ONLY the SMS-phone webhook + admin endpoints +
recovery loop, with a logging-only stub pipeline that just records what
L2 *would* have seen. It's a deployable artifact for validating the L1
plumbing end-to-end against Nathalie's phone before the full pipeline
is wired in.

Run locally:
    uvicorn app.main_l1:app --host 0.0.0.0 --port 8000

In cloud (Render / Fly / Railway / Cloud Run): use the start command in
the Dockerfile or render.yaml.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app import config
from app.admin import router as admin_router
from app.ingestion.sms_phone import router as sms_phone_router
from app.jobs.sms_poll import drain_stuck
from app.l2_lite import LitePipeline, make_default_llm_fn
from app.logging_setup import get_logger
from app.sms_inbox_store import make_sms_inbox_store

log = get_logger("main_l1")


class LoggingPipeline:
    """Stand-in for the real Pipeline until L2 is wired. Logs every
    inbound and returns the canonical success shape so the L1 layer
    can be validated end-to-end."""

    def __init__(self) -> None:
        self.received: list[tuple[str, dict[str, Any]]] = []

    async def handle_inbound(
        self, channel: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.received.append((channel, payload))
        log.info(
            "l2_would_run",
            channel=channel,
            msg_id=payload.get("msg_id"),
            from_number=payload.get("from_number"),
            body_len=len(payload.get("body", "")),
        )
        return {
            "status": "ok",
            "message_id": payload.get("msg_id"),
            "deliveries": [],
        }


async def _sms_poll_loop(app: FastAPI, interval: float) -> None:
    while True:
        try:
            await drain_stuck(app.state.sms_inbox_store, app.state.pipeline)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover - operational
            # One-line warning, not a full traceback every tick — a DB
            # outage would otherwise flood the logs. pool_pre_ping makes
            # the next tick reconnect automatically once the DB is back.
            log.warning("sms_poll_iteration_failed", error=repr(e))
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_url = os.environ.get("DB_URL", "memory")
    app.state.sms_inbox_store = make_sms_inbox_store(db_url)
    # L2-lite: rule-based classify + route + contact memory, with the
    # LLM fallback active only when ANTHROPIC_API_KEY is set AND the
    # daily budget has headroom. Replaces the LoggingPipeline stub.
    app.state.pipeline = LitePipeline(
        store=app.state.sms_inbox_store,
        llm_fn=make_default_llm_fn(),
    )
    app.state.poll_task = asyncio.create_task(
        _sms_poll_loop(app, interval=float(config.SMS_POLL_INTERVAL_SECONDS))
    )
    log.info(
        "main_l1_startup",
        db_url=("postgres" if db_url != "memory" else "memory"),
        poll_interval=config.SMS_POLL_INTERVAL_SECONDS,
        pipeline="l2_lite",
        llm_enabled=config.LLM_ENABLED,
    )
    yield
    app.state.poll_task.cancel()
    try:
        await app.state.poll_task
    except (asyncio.CancelledError, Exception):
        pass
    log.info("main_l1_shutdown")


app = FastAPI(
    title="Apteker AI Issue Router — L1 SMS-Phone",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(sms_phone_router)
app.include_router(admin_router)


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    """Liveness + DB reachability.

    Always returns HTTP 200 so a transient DB outage doesn't make Render
    kill the deploy and crash-loop. The 'db' field reports the real state:
      - "memory"   : in-process store
      - "postgres" : DB reachable, counts included
      - "error"    : DB configured but unreachable (degraded; service stays up)
    """
    using_pg = os.environ.get("DB_URL", "memory") not in ("", "memory")
    try:
        counts = app.state.sms_inbox_store.counts_by_status()
        return {
            "ok": True,
            "layer": "l1_sms_phone",
            "db": "postgres" if using_pg else "memory",
            "counts": counts,
        }
    except Exception as exc:  # noqa: BLE001 - degrade, never crash the healthcheck
        log.error("healthz_db_unreachable", error=repr(exc))
        return {
            "ok": True,
            "layer": "l1_sms_phone",
            "db": "error",
            "error": "database unreachable",
            "counts": {},
        }


@app.get("/")
async def root() -> HTMLResponse:
    return HTMLResponse(
        "<h1>Apteker AI Issue Router — L1 SMS-Phone</h1>"
        "<p>POST <code>/webhooks/sms_phone</code> with bearer auth.</p>"
        "<p>Health: <a href='/healthz'>/healthz</a></p>"
    )
