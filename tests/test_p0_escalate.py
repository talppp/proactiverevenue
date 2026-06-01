"""P0 escalation sweep - second push at +5 min, voice call at +10 min."""
from datetime import timedelta

import pytest

from app.jobs.p0_escalate import sweep_once

pytestmark = pytest.mark.asyncio


async def _post_p0(pipeline, body="EMERGENCY - flood at 456 Oak Ave Sandy Springs!"):
    payload = {"msg_id": "ESC-1", "sender_phone": "+14045550199", "body": body}
    return await pipeline.handle_inbound("sms", payload)


async def test_no_escalation_when_acknowledged(pipeline):
    res = await _post_p0(pipeline)
    assert res["urgency"] == "P0"
    # Simulate dashboard ACK by inserting the audit event the handler writes.
    pipeline.store.insert_audit(
        actor="member", action="dashboard.marked_handled",
        entity_kind="inbound_message", entity_id=res["message_id"], payload={},
    )
    stats = await sweep_once(pipeline.store)
    assert stats["second_push"] == 0
    assert stats["voice_call"] == 0


async def test_second_push_after_5_minutes(pipeline):
    await _post_p0(pipeline)
    # Push the original delivery's sent_at back by 6 minutes.
    for d in pipeline.store.deliveries.values():
        d.sent_at = d.sent_at - timedelta(minutes=6)
    stats = await sweep_once(pipeline.store)
    assert stats["second_push"] >= 1
    assert stats["voice_call"] == 0


async def test_voice_call_after_10_minutes(pipeline):
    await _post_p0(pipeline, body="EMERGENCY - gas leak at 555 Riverbend!")
    for d in pipeline.store.deliveries.values():
        d.sent_at = d.sent_at - timedelta(minutes=11)
    stats = await sweep_once(pipeline.store)
    # Both 5-min and 10-min thresholds tripped in one sweep.
    assert stats["voice_call"] == 1


async def test_voice_call_capped(pipeline):
    """Run the sweep 5 times; voice calls must cap at 3 per incident."""
    await _post_p0(pipeline, body="EMERGENCY - locked out at 1234 Peachtree!")
    voice_calls_seen = 0
    for _ in range(5):
        # Each sweep: pretend it's another 11 minutes later.
        for d in pipeline.store.deliveries.values():
            d.sent_at = d.sent_at - timedelta(minutes=11)
        stats = await sweep_once(pipeline.store)
        voice_calls_seen += stats["voice_call"]
    assert voice_calls_seen <= 3, "escalation must cap at 3 voice calls"
