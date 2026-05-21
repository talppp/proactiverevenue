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
            log.exception("sms_poll_iteration_failed", error=str(e))
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_url = os.environ.get("DB_URL", "memory")
    app.state.sms_inbox_store = make_sms_inbox_store(db_url)
    app.state.pipeline = LoggingPipeline()
    app.state.poll_task = asyncio.create_task(
        _sms_poll_loop(app, interval=float(config.SMS_POLL_INTERVAL_SECONDS))
    )
    log.info(
        "main_l1_startup",
        db_url=("postgres" if db_url != "memory" else "memory"),
        poll_interval=config.SMS_POLL_INTERVAL_SECONDS,
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
    counts = app.state.sms_inbox_store.counts_by_status()
    return {
        "ok": True,
        "layer": "l1_sms_phone",
        "db": "postgres" if os.environ.get("DB_URL", "memory") != "memory" else "memory",
        "counts": counts,
    }


@app.get("/")
async def root() -> HTMLResponse:
    return HTMLResponse(
        "<h1>Apteker AI Issue Router — L1 SMS-Phone</h1>"
        "<p>POST <code>/webhooks/sms_phone</code> with bearer auth.</p>"
        "<p>Health: <a href='/healthz'>/healthz</a></p>"
    )
