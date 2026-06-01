"""L4 rules engine unit tests - the core routing decisions."""
from datetime import UTC, datetime

from app.router.rules import decide


def _decision(**overrides):
    """Build a decide(...) call with sane defaults; override fields per test."""
    defaults = {
        "topic": "buyer_lead", "secondary_topic": None,
        "urgency": "P2", "is_business": True,
        "topic_confidence": 0.8, "urgency_confidence": 0.8,
        "county": "Fulton", "is_vip": False,
        "received_at": datetime(2026, 5, 18, 14, 0, tzinfo=UTC),   # mid-day ET
    }
    defaults.update(overrides)
    return decide(**defaults)


def test_buyer_lead_fulton_routes_to_nathalie():
    plan = _decision(topic="buyer_lead", county="Fulton")
    assert plan.primary_owner == "Nathalie"
    assert plan.is_archive is False
    assert plan.is_unsure is False
    assert plan.rule_id_matched == "topic.buyer_lead"


def test_buyer_lead_cobb_routes_to_fabi_via_geo_override():
    plan = _decision(topic="buyer_lead", county="Cobb")
    assert plan.primary_owner == "Fabi"
    assert "fabi_county" in plan.rule_id_matched


def test_seller_lead_gwinnett_routes_to_fabi():
    plan = _decision(topic="seller_lead", county="Gwinnett")
    assert plan.primary_owner == "Fabi"


def test_contract_routes_to_noa_regardless_of_county():
    plan = _decision(topic="contract", county="Cobb")
    assert plan.primary_owner == "Noa"


def test_admin_routes_to_noa():
    plan = _decision(topic="admin", county="DeKalb")
    assert plan.primary_owner == "Noa"


def test_spam_is_archived():
    plan = _decision(topic="spam", is_business=False)
    assert plan.is_archive is True
    assert plan.primary_owner is None


def test_personal_is_archived():
    plan = _decision(topic="personal", is_business=False)
    assert plan.is_archive is True


def test_low_confidence_routes_to_nathalie_unsure():
    plan = _decision(topic="buyer_lead", topic_confidence=0.4)
    assert plan.primary_owner == "Nathalie"
    assert plan.is_unsure is True


def test_vip_overrides_topic():
    plan = _decision(topic="maintenance", is_vip=True)
    assert plan.primary_owner == "Nathalie"
    assert plan.rule_id_matched == "vip.override"


def test_p0_emergency_includes_sms_owner_delivery():
    plan = _decision(topic="tenant_issue", urgency="P0", county="Fulton")
    assert "sms_owner" in plan.deliver
    assert "push_now" in plan.deliver


def test_p3_backlog_only_digest():
    plan = _decision(urgency="P3")
    assert plan.deliver == ["batched_digest"]


def test_tenant_issue_keeps_nathalie_even_in_cobb():
    """Geo override only flips buyer_lead / seller_lead / showing.
    tenant_issue must stay with its primary owner (Nathalie)."""
    plan = _decision(topic="tenant_issue", county="Cobb")
    assert plan.primary_owner == "Nathalie"
    assert "geo" not in plan.rule_id_matched
