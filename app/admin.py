"""Admin endpoints for sms_inbox_raw.

- POST /admin/sms_inbox_raw/{msg_id}/replay — re-runs a dead-letter row.
- POST /admin/sms_inbox_raw/inject — backfills a message Nathalie spotted
  on her phone that didn't make it into the staging table.
- GET  /admin/sms_inbox_raw/health — count by status, for the dashboard.

All endpoints require Authorization: Bearer ADMIN_BEARER_TOKEN.
"""

from __future__ import annotations

import hmac
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app import config
from app.ingestion.sms_phone import _get_store, _handoff
from app.logging_setup import get_logger
from app.sms_inbox_store import SmsInboxStore

log = get_logger("admin")
router = APIRouter(prefix="/admin")


def _verify_admin(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    expected = config.ADMIN_BEARER_TOKEN
    if not expected:
        raise HTTPException(status_code=503, detail="admin not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer")
    presented = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="bad bearer")


class InjectPayload(BaseModel):
    from_number: str = Field(..., min_length=1, max_length=64)
    to_number: str | None = Field(default=None, max_length=64)
    body: str = Field(..., min_length=1, max_length=4096)
    received_at_phone: datetime


@router.post("/sms_inbox_raw/{msg_id}/replay")
async def replay(
    msg_id: str,
    background: BackgroundTasks,
    request: Request,
    _: None = Depends(_verify_admin),
) -> dict[str, object]:
    store: SmsInboxStore = _get_store(request)
    pipeline = request.app.state.pipeline

    if not store.reset_for_replay(msg_id):
        raise HTTPException(status_code=404, detail="no dead_letter row with that msg_id")
    row = store.fetch(msg_id)
    if row is None:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail="row vanished during replay")
    background.add_task(
        _handoff, store, pipeline, config.SMS_POLL_MAX_ATTEMPTS, row
    )
    log.info("sms_phone_replay_queued", msg_id=msg_id)
    return {"status": "queued", "msg_id": msg_id}


@router.post("/sms_inbox_raw/inject")
async def inject(
    payload: InjectPayload,
    background: BackgroundTasks,
    request: Request,
    _: None = Depends(_verify_admin),
) -> dict[str, object]:
    store: SmsInboxStore = _get_store(request)
    pipeline = request.app.state.pipeline

    row, was_new = store.insert_new(
        from_number=payload.from_number,
        to_number=payload.to_number,
        body=payload.body,
        received_at_phone=payload.received_at_phone,
    )
    if was_new:
        background.add_task(
            _handoff, store, pipeline, config.SMS_POLL_MAX_ATTEMPTS, row
        )
    log.info("sms_phone_inject", msg_id=row.msg_id, new=was_new)
    return {"status": "queued", "msg_id": row.msg_id, "duplicate": not was_new}


@router.get("/sms_inbox_raw/health")
async def health(
    request: Request,
    _: None = Depends(_verify_admin),
) -> dict[str, int]:
    store: SmsInboxStore = _get_store(request)
    return store.counts_by_status()


class FeedbackPayload(BaseModel):
    corrected_topic: str | None = Field(default=None, max_length=64)
    corrected_urgency: str | None = Field(default=None, max_length=8)
    note: str | None = Field(default=None, max_length=1000)


@router.post("/sms_inbox_raw/{msg_id}/feedback")
async def feedback(
    msg_id: str,
    payload: FeedbackPayload,
    request: Request,
    _: None = Depends(_verify_admin),
) -> dict[str, object]:
    """Record an operator correction (wrong topic/urgency/owner) against a
    message. These accumulate as labeled eval data for classifier tuning —
    the system's learning loop starts here."""
    if not any([payload.corrected_topic, payload.corrected_urgency, payload.note]):
        raise HTTPException(status_code=422, detail="empty feedback")
    store: SmsInboxStore = _get_store(request)
    if not store.insert_feedback(
        msg_id, payload.corrected_topic, payload.corrected_urgency, payload.note
    ):
        raise HTTPException(status_code=404, detail="no message with that msg_id")
    log.info("sms_feedback_recorded", msg_id=msg_id)
    return {"status": "recorded", "msg_id": msg_id}


@router.get("/feedback")
async def list_feedback(
    request: Request,
    limit: int = 50,
    _: None = Depends(_verify_admin),
) -> list[dict]:
    """Most-recent-first corrections — the eval-set export."""
    store: SmsInboxStore = _get_store(request)
    return store.list_feedback(limit=min(limit, 500))
