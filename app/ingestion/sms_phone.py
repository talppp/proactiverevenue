"""L1 SMS-phone webhook.

Receives POSTs from the iOS Shortcuts forwarder on Nathalie's iPhone,
stages each message idempotently in `sms_inbox_raw`, and hands new rows
off to the existing L2 pipeline (Pipeline.handle_inbound) via FastAPI
BackgroundTasks.

See docs/L1_SMS_Phone_Ingestion_Design.md for the full design and
docs/runbooks/sms_phone_install.md for ops.
"""

from __future__ import annotations

import hmac
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app import config
from app.logging_setup import get_logger
from app.sms_inbox_store import InMemorySmsInboxStore, SmsInboxStore, SmsRow

log = get_logger("ingestion.sms_phone")
router = APIRouter()

# Channel identifier propagated through L2-L6 so rules can distinguish
# phone-mirrored SMS from a future direct-Twilio inbound path.
CHANNEL = "sms_phone"


class SmsPhonePayload(BaseModel):
    from_number: str = Field(..., min_length=1, max_length=64)
    to_number: str | None = Field(default=None, max_length=64)
    body: str = Field(..., min_length=1, max_length=4096)
    # Optional: iOS Shortcuts can't easily emit ISO-8601, and the
    # automation fires once in real time (no auto-retry), so when the
    # phone omits this we stamp server-receipt time. Senders that DO
    # provide it (admin inject, tests, future Mac reader) still work.
    received_at_phone: datetime | None = None


def _verify_bearer(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    valid = [
        t
        for t in (config.SMS_BEARER_TOKEN_PRIMARY, config.SMS_BEARER_TOKEN_SECONDARY)
        if t
    ]
    if not valid:
        log.error("sms_phone_no_tokens_configured")
        raise HTTPException(status_code=503, detail="server not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer")
    presented = authorization.removeprefix("Bearer ").strip()
    if not any(hmac.compare_digest(presented, t) for t in valid):
        raise HTTPException(status_code=401, detail="bad bearer")


def _pipeline_payload(row: SmsRow) -> dict[str, Any]:
    """Shape the staged row into what the existing pipeline expects.

    The real `normalize_and_persist` accepts a channel-specific dict; for
    `sms_phone` we hand it the canonical fields and let it normalise.
    Integrator note (see runbook): add 'sms_phone' to normalize.py's
    channel dispatch table — payload shape is identical to 'sms' apart
    from the channel string.
    """
    return {
        "msg_id": row.msg_id,
        "provider_msg_id": row.msg_id,
        "from_number": row.from_number,
        "to_number": row.to_number,
        "body": row.body,
        "received_at": row.received_at_phone.isoformat(),
    }


async def _handoff(
    store: SmsInboxStore,
    pipeline: Any,
    max_attempts: int,
    row: SmsRow,
) -> None:
    try:
        result = await pipeline.handle_inbound(CHANNEL, _pipeline_payload(row))
        # The existing pipeline returns a dict with a 'status' key; treat
        # 'duplicate' / 'rejected' as a successful handoff (it's gotten
        # past L1, just nothing for L2 to do downstream).
        if isinstance(result, dict) and result.get("status") == "rejected":
            store.mark_attempt_failed(
                row.msg_id,
                f"pipeline rejected: {result.get('reason', '')}",
                max_attempts,
            )
            log.warning(
                "sms_phone_pipeline_rejected",
                msg_id=row.msg_id, reason=result.get("reason"),
            )
            return
    except Exception as exc:  # noqa: BLE001
        new_status = store.mark_attempt_failed(row.msg_id, repr(exc), max_attempts)
        log.warning(
            "sms_phone_handoff_failed",
            msg_id=row.msg_id, status=new_status, error=repr(exc),
        )
        return
    store.mark_handed_off(row.msg_id)
    log.info("sms_phone_handed_off", msg_id=row.msg_id)


def _get_store(request: Request) -> SmsInboxStore:
    """Lazily create the staging store if main.py hasn't set one yet.

    In the real project, main.py's lifespan attaches the store at startup.
    Tests inject their own. This fallback keeps the module importable in
    isolation.
    """
    store = getattr(request.app.state, "sms_inbox_store", None)
    if store is None:
        store = InMemorySmsInboxStore()
        request.app.state.sms_inbox_store = store
    return store


@router.post("/webhooks/sms_phone")
async def receive_sms_phone(
    payload: SmsPhonePayload,
    background: BackgroundTasks,
    request: Request,
    _: None = Depends(_verify_bearer),
) -> dict[str, object]:
    store = _get_store(request)
    pipeline = request.app.state.pipeline

    received_at_phone = payload.received_at_phone or datetime.now(timezone.utc)
    row, was_new = store.insert_new(
        from_number=payload.from_number,
        to_number=payload.to_number,
        body=payload.body,
        received_at_phone=received_at_phone,
    )
    log.info(
        "sms_phone_received",
        msg_id=row.msg_id,
        from_number=payload.from_number,
        body_len=len(payload.body),
        new=was_new,
    )

    if was_new:
        background.add_task(
            _handoff, store, pipeline, config.SMS_POLL_MAX_ATTEMPTS, row
        )

    return {"status": "queued", "msg_id": row.msg_id, "duplicate": not was_new}
