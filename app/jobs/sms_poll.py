"""sms_inbox_raw recovery loop.

Real-time delivery happens in-process inside the webhook (see
ingestion/sms_phone.py). This job exists only to drain rows the
in-process handoff couldn't complete — pipeline crashes mid-flight,
deploys, transient L2 errors. It looks for rows still at status='new'
more than SMS_STUCK_THRESHOLD_SECONDS after received_at and replays them
through the same Pipeline.handle_inbound() call.

After SMS_POLL_MAX_ATTEMPTS failures, a row moves to status='dead_letter'
for manual replay via /admin/sms_inbox_raw/{msg_id}/replay.
"""

from __future__ import annotations

from typing import Any

from app import config
from app.ingestion.sms_phone import CHANNEL, _pipeline_payload
from app.logging_setup import get_logger
from app.sms_inbox_store import SmsInboxStore

log = get_logger("jobs.sms_poll")


async def drain_stuck(store: SmsInboxStore, pipeline: Any) -> int:
    """One pass of the recovery loop. Returns the number of rows
    successfully handed off this tick. Per-row exceptions are recorded
    and do not abort the batch."""
    stuck = store.fetch_stuck(
        threshold_seconds=config.SMS_STUCK_THRESHOLD_SECONDS,
        batch_size=config.SMS_POLL_BATCH_SIZE,
    )
    if not stuck:
        return 0

    handed_off = 0
    for row in stuck:
        try:
            result = await pipeline.handle_inbound(CHANNEL, _pipeline_payload(row))
        except Exception as exc:  # noqa: BLE001
            new_status = store.mark_attempt_failed(
                row.msg_id, repr(exc), config.SMS_POLL_MAX_ATTEMPTS
            )
            log.warning(
                "sms_poll_handoff_failed",
                msg_id=row.msg_id, status=new_status, error=repr(exc),
            )
            continue
        if isinstance(result, dict) and result.get("status") == "rejected":
            store.mark_attempt_failed(
                row.msg_id,
                f"pipeline rejected: {result.get('reason', '')}",
                config.SMS_POLL_MAX_ATTEMPTS,
            )
            log.warning(
                "sms_poll_pipeline_rejected",
                msg_id=row.msg_id, reason=result.get("reason"),
            )
            continue
        store.mark_handed_off(row.msg_id)
        handed_off += 1

    log.info("sms_poll_drained", handed_off=handed_off, stuck=len(stuck))
    return handed_off
