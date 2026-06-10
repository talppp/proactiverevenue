from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.l2_lite import (
    BudgetGuard,
    LitePipeline,
    classify_topic,
    classify_urgency,
    route_owner,
)

_RCV = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- classifier
def test_urgency_p0_emergency():
    urgency, conf = classify_urgency("EMERGENCY - burst pipe flooding the basement!")
    assert urgency == "P0"
    assert conf >= 0.6


def test_urgency_p1_deadline():
    urgency, _ = classify_urgency("Need this signed today, deadline is 5pm ASAP")
    assert urgency == "P1"


def test_urgency_p3_no_rush():
    urgency, _ = classify_urgency("No rush, just FYI for next month")
    assert urgency == "P3"


def test_urgency_default_p2():
    urgency, conf = classify_urgency("Can you call me back about the house")
    assert urgency == "P2"
    assert conf == 0.6


def test_topic_showing():
    topic, conf = classify_topic("Can we do a showing Saturday? Maybe an open house?")
    assert topic == "showing"
    assert conf > 0.55


def test_topic_maintenance():
    topic, _ = classify_topic("The AC stopped working and the faucet has a leak")
    assert topic == "maintenance"


def test_topic_unknown_low_confidence():
    topic, conf = classify_topic("hey")
    assert topic == "unknown"
    assert conf == 0.3  # below CONFIDENCE_FLOOR -> is_unsure


def test_routing_back_office_topics_to_noa():
    assert route_owner("billing", "P2") == ("noa", "nathalie")
    assert route_owner("contract", "P2") == ("noa", "nathalie")


def test_routing_p0_always_nathalie():
    # maintenance normally routes to noa, but P0 goes to the
    # after-hours-ok owner
    primary, _ = route_owner("maintenance", "P0")
    assert primary == "nathalie"


# ---------------------------------------------------------------- budget
def test_budget_guard_enforces_ceiling(store):
    guard = BudgetGuard(store, daily_ceiling_usd=0.01)
    assert guard.can_spend(0.005) is True
    guard.record(0.005)
    assert guard.can_spend(0.005) is True   # exactly at ceiling
    guard.record(0.005)
    assert guard.can_spend(0.001) is False  # over -> refused


def test_budget_spend_accumulates(store):
    guard = BudgetGuard(store, daily_ceiling_usd=10.0)
    assert guard.record(0.25) == pytest.approx(0.25)
    assert guard.record(0.25) == pytest.approx(0.50)
    assert store.llm_spend_today() == pytest.approx(0.50)


# ---------------------------------------------------------------- pipeline
@pytest.mark.asyncio
async def test_lite_pipeline_classifies_and_routes(store):
    pipe = LitePipeline(store=store, llm_fn=None)
    result = await pipe.handle_inbound(
        "sms_phone",
        {"msg_id": "m1", "from_number": "+14045551234",
         "body": "Burst pipe! water everywhere, emergency"},
    )
    assert result["status"] == "ok"
    assert result["urgency"] == "P0"
    assert result["owner"] == "nathalie"
    assert result["is_unsure"] is False
    assert result["used_llm"] is False


@pytest.mark.asyncio
async def test_lite_pipeline_flags_unsure(store):
    pipe = LitePipeline(store=store, llm_fn=None, confidence_floor=0.55)
    result = await pipe.handle_inbound(
        "sms_phone",
        {"msg_id": "m2", "from_number": "+14045551234", "body": "hey"},
    )
    assert result["topic"] == "unknown"
    assert result["is_unsure"] is True
    assert result["owner"] == "nathalie"  # unsure -> owner reviews


@pytest.mark.asyncio
async def test_lite_pipeline_thread_depth_from_contact_memory(store):
    # Stage two prior messages from the same sender.
    for i, body in enumerate(["earlier msg one", "earlier msg two"]):
        store.insert_new(
            from_number="+14045559999", to_number=None, body=body,
            received_at_phone=_RCV.replace(minute=i),
        )
    pipe = LitePipeline(store=store, llm_fn=None)
    result = await pipe.handle_inbound(
        "sms_phone",
        {"msg_id": "m3", "from_number": "+14045559999",
         "body": "following up on the offer"},
    )
    assert result["thread_depth"] == 2
    assert result["topic"] == "deal"


@pytest.mark.asyncio
async def test_lite_pipeline_llm_fallback_called_when_unsure(store):
    calls = []

    def fake_llm(body, context):
        calls.append((body, context))
        return ("P1", "buyer_lead", 0.9, 0.0015)

    pipe = LitePipeline(store=store, llm_fn=fake_llm,
                        confidence_floor=0.55, daily_ceiling_usd=1.0)
    result = await pipe.handle_inbound(
        "sms_phone",
        {"msg_id": "m4", "from_number": "+14045551234", "body": "hey"},
    )
    assert len(calls) == 1
    assert result["used_llm"] is True
    assert result["topic"] == "buyer_lead"
    assert result["is_unsure"] is False
    assert store.llm_spend_today() == pytest.approx(0.0015)


@pytest.mark.asyncio
async def test_lite_pipeline_llm_refused_when_budget_spent(store):
    calls = []

    def fake_llm(body, context):
        calls.append(body)
        return ("P1", "buyer_lead", 0.9, 0.0015)

    pipe = LitePipeline(store=store, llm_fn=fake_llm,
                        confidence_floor=0.55, daily_ceiling_usd=0.001)
    store.add_llm_spend(0.001)  # day budget already gone
    result = await pipe.handle_inbound(
        "sms_phone",
        {"msg_id": "m5", "from_number": "+14045551234", "body": "hey"},
    )
    assert calls == []                 # never invoked
    assert result["used_llm"] is False
    assert result["is_unsure"] is True  # rule result stands, flagged


@pytest.mark.asyncio
async def test_lite_pipeline_llm_skipped_when_rules_confident(store):
    def fake_llm(body, context):  # pragma: no cover - must not run
        raise AssertionError("LLM called despite confident rules")

    pipe = LitePipeline(store=store, llm_fn=fake_llm, confidence_floor=0.55)
    result = await pipe.handle_inbound(
        "sms_phone",
        {"msg_id": "m6", "from_number": "+14045551234",
         "body": "burst pipe emergency, need a showing rescheduled"},
    )
    assert result["used_llm"] is False


# ---------------------------------------------------------------- feedback
def test_feedback_endpoint_records_and_lists(client, store):
    from app.sms_inbox_store import compute_msg_id

    rcv = datetime(2026, 5, 30, 11, 30, 0, tzinfo=timezone.utc)
    store.insert_new(
        from_number="+14045551234", to_number=None,
        body="misclassified message", received_at_phone=rcv,
    )
    msg_id = compute_msg_id("+14045551234", "misclassified message", rcv)
    admin = {"Authorization": "Bearer admin-secret"}

    r = client.post(
        f"/admin/sms_inbox_raw/{msg_id}/feedback",
        json={"corrected_topic": "billing", "note": "this was an invoice question"},
        headers=admin,
    )
    assert r.status_code == 200

    r2 = client.get("/admin/feedback", headers=admin)
    assert r2.status_code == 200
    items = r2.json()
    assert len(items) == 1
    assert items[0]["corrected_topic"] == "billing"


def test_feedback_404_for_unknown_msg(client):
    r = client.post(
        "/admin/sms_inbox_raw/does-not-exist/feedback",
        json={"corrected_topic": "billing"},
        headers={"Authorization": "Bearer admin-secret"},
    )
    assert r.status_code == 404


def test_feedback_422_when_empty(client, store):
    from app.sms_inbox_store import compute_msg_id

    rcv = datetime(2026, 5, 30, 11, 31, 0, tzinfo=timezone.utc)
    store.insert_new(
        from_number="+14045551234", to_number=None,
        body="another message", received_at_phone=rcv,
    )
    msg_id = compute_msg_id("+14045551234", "another message", rcv)
    r = client.post(
        f"/admin/sms_inbox_raw/{msg_id}/feedback",
        json={},
        headers={"Authorization": "Bearer admin-secret"},
    )
    assert r.status_code == 422
