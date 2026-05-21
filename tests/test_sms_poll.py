from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.jobs import sms_poll
from app.sms_inbox_store import compute_msg_id


@pytest.mark.asyncio
async def test_drain_stuck_skips_fresh_rows(store, pipeline):
    row, _ = store.insert_new(
        from_number="+14045551234",
        to_number=None,
        body="fresh",
        received_at_phone=datetime(2026, 5, 21, 11, 59, 59, tzinfo=timezone.utc),
    )
    handed = await sms_poll.drain_stuck(store, pipeline)
    assert handed == 0
    assert store.fetch(row.msg_id).status == "new"


@pytest.mark.asyncio
async def test_drain_stuck_picks_up_aged_rows(store, pipeline, now_func):
    row, _ = store.insert_new(
        from_number="+14045551234",
        to_number=None,
        body="stuck",
        received_at_phone=datetime(2026, 5, 21, 11, 59, 59, tzinfo=timezone.utc),
    )
    now_func.advance(60)
    handed = await sms_poll.drain_stuck(store, pipeline)
    assert handed == 1
    assert store.fetch(row.msg_id).status == "handed_off"
    assert len(pipeline.received) == 1
    channel, payload = pipeline.received[0]
    assert channel == "sms_phone"
    assert payload["from_number"] == "+14045551234"


@pytest.mark.asyncio
async def test_drain_stuck_dead_letters_after_max_attempts(store, pipeline, now_func):
    received_at_phone = datetime(2026, 5, 21, 11, 59, 59, tzinfo=timezone.utc)
    msg_id = compute_msg_id("+14045551234", "boom", received_at_phone)
    pipeline.fail_for_msg_ids.add(msg_id)

    store.insert_new(
        from_number="+14045551234",
        to_number=None,
        body="boom",
        received_at_phone=received_at_phone,
    )

    for _ in range(3):  # patched SMS_POLL_MAX_ATTEMPTS = 3
        now_func.advance(60)
        await sms_poll.drain_stuck(store, pipeline)

    row = store.fetch(msg_id)
    assert row.status == "dead_letter"
    assert row.attempts == 3


@pytest.mark.asyncio
async def test_drain_stuck_continues_past_individual_failures(store, pipeline, now_func):
    bad_received = datetime(2026, 5, 21, 11, 0, 0, tzinfo=timezone.utc)
    bad_id = compute_msg_id("+14045551111", "bad", bad_received)
    pipeline.fail_for_msg_ids.add(bad_id)

    store.insert_new(
        from_number="+14045551111",
        to_number=None,
        body="bad",
        received_at_phone=bad_received,
    )
    store.insert_new(
        from_number="+14045552222",
        to_number=None,
        body="good",
        received_at_phone=datetime(2026, 5, 21, 11, 1, 0, tzinfo=timezone.utc),
    )

    now_func.advance(120)
    handed = await sms_poll.drain_stuck(store, pipeline)
    assert handed == 1  # only the good one
    counts = store.counts_by_status()
    assert counts["handed_off"] == 1
    assert counts["new"] == 1  # bad one still pending retry


@pytest.mark.asyncio
async def test_drain_stuck_handles_pipeline_rejection(store, pipeline, now_func):
    """Pipeline returning {'status': 'rejected'} counts as a failure."""

    async def reject(channel, payload):
        return {"status": "rejected", "reason": "unknown channel"}

    pipeline.handle_inbound = reject  # type: ignore[method-assign]

    row, _ = store.insert_new(
        from_number="+14045551234",
        to_number=None,
        body="rejected",
        received_at_phone=datetime(2026, 5, 21, 11, 59, 59, tzinfo=timezone.utc),
    )
    now_func.advance(60)
    handed = await sms_poll.drain_stuck(store, pipeline)
    assert handed == 0
    row2 = store.fetch(row.msg_id)
    assert row2.status == "new"
    assert row2.attempts == 1
    assert "rejected" in (row2.last_error or "")
