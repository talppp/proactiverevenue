"""
L4 - Rules engine.

Loads routing_rules.yaml once and evaluates routing for a given
(classification, geo) pair.

Evaluation order (from the YAML and the human-readable spec):
  1.   Hard non-business override -> archive.
  1.5  Language override (Hebrew/Spanish) -> Nathalie unconditionally.
  2.   VIP override               -> Nathalie.
  3.   Confidence floor           -> Nathalie with is_unsure=true.
  4.   Geo override               -> Fabi for lead/showing topics in her counties.
  5.   Topic routing table        -> primary owner + fallback.
  6.   After-hours policy         -> may swap to who's after_hours_ok.

Returns a RoutingPlan: which team-member NAME owns this message, plus an
optional fallback, plus the rule_id matched and a human-readable reason.

L5 (assign.py) then picks the actual team_member row, honouring capacity
and availability windows; that final step writes the routing_decision row.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

import yaml

from app.config import CONFIDENCE_FLOOR, ROUTING_RULES_PATH
from app.logging_setup import get_logger

log = get_logger("L4_rules")


# Cached rules (loaded once at startup).  Reload by calling reload_rules().
_RULES: dict | None = None


def reload_rules() -> dict:
    global _RULES
    raw = ROUTING_RULES_PATH.read_text(encoding="utf-8")
    _RULES = yaml.safe_load(raw)
    log.info("rules_loaded", file=str(ROUTING_RULES_PATH),
             topics=len(_RULES.get("topic_routing", [])))
    return _RULES


def rules() -> dict:
    return _RULES if _RULES is not None else reload_rules()


# Topics whose ownership can be overridden by the geo rule.
_GEO_FLIPPABLE = {"buyer_lead", "seller_lead", "showing"}

# Topics that always go to back-office regardless of county.
_BACK_OFFICE = {"contract", "document", "vendor", "admin", "scheduling", "billing"}


@dataclass
class RoutingPlan:
    primary_owner: str | None      # display_name
    fallback_owner: str | None
    rule_id_matched: str
    reason: str
    is_archive: bool
    is_unsure: bool
    deliver: list[str]             # delivery kinds suggested by urgency tier


def _delivery_for_urgency(urgency: str) -> list[str]:
    table = rules().get("urgency_rules", {})
    if urgency == "P0":
        return list(table.get("P0_emergency", {}).get("deliver", ["push_now", "sms_owner"]))
    if urgency == "P1":
        return list(table.get("P1_same_day", {}).get("deliver", ["push_now", "queue_card"]))
    if urgency == "P2":
        return list(table.get("P2_24h", {}).get("deliver", ["queue_card", "batched_digest"]))
    return list(table.get("P3_backlog", {}).get("deliver", ["batched_digest"]))


def _topic_primary(topic: str) -> tuple[str | None, str | None]:
    for row in rules().get("topic_routing", []):
        if row.get("topic") == topic:
            return row.get("primary_owner"), row.get("fallback")
    return None, None


def _team_member(name: str | None) -> dict | None:
    if not name:
        return None
    team = rules().get("team", {})
    return team.get(name.lower())


def _is_after_hours(received_at: datetime | None) -> bool:
    """Apply Apteker's 19:00 - 08:00 ET window. Naive timezone math is OK
    here - we use a fixed offset since the YAML is fixed at America/New_York."""
    if received_at is None:
        return False
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=UTC)
    # Convert to ET (EST=UTC-5 / EDT=UTC-4 - good enough for the routing decision).
    et = received_at.astimezone(timezone(timedelta(hours=-4)))
    h = et.hour
    return h >= 19 or h < 8


def decide(*,
           topic: str,
           secondary_topic: str | None,
           urgency: str,
           is_business: bool,
           topic_confidence: float,
           urgency_confidence: float,
           county: str | None,
           is_vip: bool,
           received_at: datetime | None,
           language: str = "en",
           ) -> RoutingPlan:
    """Pure function - returns a RoutingPlan based purely on the inputs and
    the YAML rules. No DB I/O.

    Evaluation order:
      1.   Non-business  -> archive.
      1.5  Language override (Hebrew 'he' / Spanish 'es') -> Nathalie.
      2.   VIP override  -> Nathalie.
      3.   Confidence floor -> Nathalie + is_unsure=True.
      4.   Back-office   -> Noa.
      5.   Geo override  -> Fabi for lead/showing in her counties.
      6.   Topic primary.
    """

    # 1) Non-business -> archive.
    if not is_business or topic in ("personal", "marketing_promo", "spam"):
        return RoutingPlan(
            primary_owner=None, fallback_owner=None,
            rule_id_matched="filter.non_business",
            reason=f"topic={topic} marked non-business",
            is_archive=True, is_unsure=False,
            deliver=["archive"],
        )

    # 1.5) Language override: Hebrew always to Nathalie (taxonomy rule T5);
    #      Spanish as graceful fallback (Nathalie is the only bilingual owner).
    if language in ("he", "es"):
        lang_label = "Hebrew" if language == "he" else "Spanish"
        return RoutingPlan(
            primary_owner="Nathalie", fallback_owner=None,
            rule_id_matched=f"language.{language}",
            reason=f"{lang_label}-language message routed to Nathalie (taxonomy rule T5)",
            is_archive=False, is_unsure=False,
            deliver=_delivery_for_urgency(urgency),
        )

    # 2) VIP override.
    if is_vip:
        return RoutingPlan(
            primary_owner="Nathalie", fallback_owner=None,
            rule_id_matched="vip.override",
            reason="sender is in VIP list",
            is_archive=False, is_unsure=False,
            deliver=_delivery_for_urgency(urgency),
        )

    # 3) Confidence floor.
    min_conf = min(topic_confidence, urgency_confidence)
    if min_conf < CONFIDENCE_FLOOR:
        return RoutingPlan(
            primary_owner="Nathalie", fallback_owner=None,
            rule_id_matched="confidence.floor",
            reason=f"min confidence {min_conf:.2f} < floor {CONFIDENCE_FLOOR}",
            is_archive=False, is_unsure=True,
            deliver=_delivery_for_urgency(urgency),
        )

    # 4) Back-office topics -> Noa regardless of county.
    if topic in _BACK_OFFICE:
        primary, fallback = _topic_primary(topic)
        return RoutingPlan(
            primary_owner=(primary or "noa").capitalize(),
            fallback_owner=(fallback or "nathalie").capitalize(),
            rule_id_matched=f"topic.{topic}",
            reason="back-office topic; geo-agnostic",
            is_archive=False, is_unsure=False,
            deliver=_delivery_for_urgency(urgency),
        )

    # 5) Geo override for buyer/seller/showing.
    fabi_counties = set(rules().get("team", {}).get("fabi", {}).get("counties", []))
    if topic in _GEO_FLIPPABLE and county and county in fabi_counties:
        return RoutingPlan(
            primary_owner="Fabi", fallback_owner="Nathalie",
            rule_id_matched=f"geo.fabi_county.{topic}",
            reason=f"{topic} in Fabi's county ({county})",
            is_archive=False, is_unsure=False,
            deliver=_delivery_for_urgency(urgency),
        )

    # 6) Default topic primary.
    primary, fallback = _topic_primary(topic)
    if not primary:
        primary = "nathalie"
    return RoutingPlan(
        primary_owner=primary.capitalize(),
        fallback_owner=(fallback or "nathalie").capitalize(),
        rule_id_matched=f"topic.{topic}",
        reason="topic primary owner",
        is_archive=False, is_unsure=False,
        deliver=_delivery_for_urgency(urgency),
    )
