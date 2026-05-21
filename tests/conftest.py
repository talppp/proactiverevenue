"""Test scaffolding for the L1 SMS-phone path.

The full project's `app.main` imports modules that aren't in this repo
(the real classifier/router/delivery layers live in the Phase 1 Drive
project), so we build a minimal FastAPI app here that mounts only the
new routers under test and stubs the pipeline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config as app_config
from app.admin import router as admin_router
from app.ingestion.sms_phone import router as sms_phone_router
from app.sms_inbox_store import InMemorySmsInboxStore


class StubPipeline:
    """Implements the project's `Pipeline.handle_inbound(channel, payload)`
    signature; tests assert against `.received` and can mark `msg_id`s to
    fail by adding them to `.fail_for_msg_ids`."""

    def __init__(self) -> None:
        self.received: list[tuple[str, dict[str, Any]]] = []
        self.fail_for_msg_ids: set[str] = set()

    async def handle_inbound(self, channel: str, payload: dict[str, Any]) -> dict[str, Any]:
        msg_id = payload.get("msg_id") or payload.get("provider_msg_id") or ""
        if msg_id in self.fail_for_msg_ids:
            raise RuntimeError(f"stub failure for {msg_id}")
        self.received.append((channel, payload))
        return {"status": "ok", "message_id": msg_id}


@pytest.fixture
def now_func():
    """Mutable clock: tests advance `state['t']` to simulate row aging."""
    state = {"t": datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)}

    def fn() -> datetime:
        return state["t"]

    fn.advance = lambda seconds: state.update(t=state["t"] + timedelta(seconds=seconds))  # type: ignore[attr-defined]
    return fn


@pytest.fixture
def store(now_func) -> InMemorySmsInboxStore:
    return InMemorySmsInboxStore(_now=now_func)


@pytest.fixture
def pipeline() -> StubPipeline:
    return StubPipeline()


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    """Tighten the timeouts and supply test tokens for every test."""
    monkeypatch.setattr(app_config, "SMS_BEARER_TOKEN_PRIMARY", "primary-secret")
    monkeypatch.setattr(app_config, "SMS_BEARER_TOKEN_SECONDARY", "secondary-secret")
    monkeypatch.setattr(app_config, "ADMIN_BEARER_TOKEN", "admin-secret")
    monkeypatch.setattr(app_config, "SMS_POLL_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(app_config, "SMS_STUCK_THRESHOLD_SECONDS", 30)
    monkeypatch.setattr(app_config, "SMS_POLL_BATCH_SIZE", 50)


@pytest.fixture
def app(store, pipeline) -> FastAPI:
    application = FastAPI()
    application.state.sms_inbox_store = store
    application.state.pipeline = pipeline
    application.include_router(sms_phone_router)
    application.include_router(admin_router)
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c
