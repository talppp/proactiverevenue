"""End-to-end pipeline test - exercises L1 -> L6 in one async call.

Uses the in-memory store and the stub classifier so this test runs
in CI with no Postgres, no Anthropic key, no external dependencies.
"""
import pytest

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("body,expected_owner,expected_urgency,expected_archive", [
    # P0 emergency from the dummy test user -> Nathalie
    ("EMERGENCY - water is pouring from the ceiling at 456 Oak Ave Sandy Springs!",
     "Nathalie", "P0", False),

    # Buyer asking about a Cobb-county property -> Fabi (geo override)
    ("Hi, viewing a property at 555 Riverbend Way Marietta. Can we see it tomorrow?",
     "Fabi", "P2", False),

    # Contract back-office -> Noa
    ("Please review the redline on the addendum for 1234 Peachtree St.",
     "Noa", "P2", False),

    # Spam -> archive
    ("CLAIM YOUR PRIZE NOW - click here: http://scam.example/",
     None, "P2", True),
])
async def test_pipeline_routes_correctly(pipeline, body, expected_owner,
                                          expected_urgency, expected_archive):
    payload = {"msg_id": f"PT-{hash(body) & 0xFFFF:04x}",
               "sender_phone": "+14045550199",
               "body": body}
    res = await pipeline.handle_inbound("sms", payload)
    assert res["status"] == "ok", res
    assert res["urgency"] == expected_urgency, res
    assert res["owner"] == expected_owner, res
    assert res["is_archive"] is expected_archive, res


async def test_pipeline_emits_audit_chain(pipeline):
    """Every layer transition writes an audit_event row."""
    payload = {"msg_id": "AT-1", "sender_phone": "+14045550199",
               "body": "EMERGENCY - flood at 789 Maple Dr Decatur!"}
    res = await pipeline.handle_inbound("sms", payload)
    audit = pipeline.store.audit
    msg_id = res["message_id"]
    actions = [e["action"] for e in audit if e.get("entity_id") == msg_id]
    assert "L2.normalized" in actions
    assert "L3.classified" in actions
    assert "L4_L5.routed"  in actions
    assert "L6.delivered"  in actions


async def test_pipeline_idempotent_delivery(pipeline):
    """Re-posting the same body within 60s short-circuits as duplicate."""
    payload = {"msg_id": "DUP-1", "sender_phone": "+14045550199",
               "body": "Hi, just checking on showing for tomorrow."}
    first = await pipeline.handle_inbound("sms", payload)
    assert first["status"] == "ok"
    second = await pipeline.handle_inbound("sms", payload)
    assert second["status"] == "duplicate"
