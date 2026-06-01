"""
L6 - Delivery.

Fans the routing decision out to delivery channels per the urgency tier.
For the demo, real WhatsApp / Twilio / Slack pushes are MOCKED - we
publish a JSON card to the WebSocket hub and log a delivery_log row.

Idempotency: each (message_id, delivery_kind, owner) tuple produces a
stable key; duplicate calls no-op via the DB UNIQUE constraint and the
Store.existing_idempotency() helper.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from app.classifier.topic import TopicResult
from app.classifier.urgency import UrgencyResult
from app.logging_setup import get_logger
from app.store import DeliveryEvent, InboundMessage, RoutingDecision, Store
from app.ws import HUB

log = get_logger("L6_deliver")


def _idempotency_key(message_id: str, kind: str, owner: str | None) -> str:
    h = hashlib.sha256()
    h.update(f"{message_id}|{kind}|{owner or ''}".encode())
    return h.hexdigest()[:32]


def _urgency_color(u: str) -> str:
    return {"P0": "#C0392B", "P1": "#E67E22",
            "P2": "#2980B9", "P3": "#7F8C8D"}.get(u, "#666666")


async def deliver(*, msg: InboundMessage,
                  urgency: UrgencyResult, topic: TopicResult,
                  decision: RoutingDecision, deliver_kinds: list[str],
                  store: Store) -> list[DeliveryEvent]:
    events: list[DeliveryEvent] = []

    # Build the dashboard card once - same content across delivery kinds.
    card: dict[str, Any] = {
        "message_id":     msg.id,
        "received_at":    msg.received_at.isoformat(),
        "channel":        msg.channel,
        "sender":         msg.sender_handle,
        "subject":        msg.subject,
        "preview":        msg.body[:280],
        "urgency":        urgency.urgency,
        "urgency_color":  _urgency_color(urgency.urgency),
        "topic":          topic.topic,
        "secondary":      topic.secondary_topic,
        "summary":        topic.summary,
        "suggested_reply": topic.suggested_reply,
        "owner":          decision.primary_owner,
        "rule":           decision.rule_id_matched,
        "reason":         decision.reason,
        "is_unsure":      decision.is_unsure,
        "is_archive":     decision.is_archive,
    }

    owner = decision.primary_owner
    target = owner.lower() if owner else "_archive"

    # Archive path: just log; no push.
    if decision.is_archive:
        ev = _record(store, decision.id, "archive", target,
                     idempotency_key=_idempotency_key(msg.id, "archive", target),
                     status="skipped")
        events.append(ev)
        log.info("delivery_archive", message_id=msg.id)
        return events

    # For each delivery_kind, push to the hub + record.
    for kind in deliver_kinds:
        key = _idempotency_key(msg.id, kind, target)
        if store.existing_idempotency(key):
            log.info("delivery_idempotent_skip", kind=kind, owner=owner)
            continue
        if kind == "archive":
            ev = _record(store, decision.id, "archive", target,
                         idempotency_key=key, status="skipped")
        elif kind in ("batched_digest",):
            # Phase 4 will pick these up; for Phase I we just record.
            ev = _record(store, decision.id, "queue_card", target,
                         idempotency_key=key, status="queued")
        else:
            # push_now / sms_owner / queue_card / ai_draft / escalate_if_unread_5min
            delivered = await HUB.publish(target, {**card, "kind": kind})
            status = "sent" if delivered > 0 else "queued"
            ev = _record(store, decision.id, kind, target,
                         idempotency_key=key, status=status)
        events.append(ev)

    store.insert_audit(
        actor="system", action="L6.delivered",
        entity_kind="inbound_message", entity_id=msg.id,
        payload={"owner": owner, "kinds": deliver_kinds,
                 "delivered": len([e for e in events if e.status == "sent"])},
    )
    return events


def _record(store: Store, routing_id: str, kind: str, target: str,
            idempotency_key: str, status: str,
            error: str | None = None) -> DeliveryEvent:
    ev = DeliveryEvent(
        id=str(uuid.uuid4()),
        routing_id=routing_id,
        delivery_kind=kind,
        target_handle=target,
        status=status,
        idempotency_key=idempotency_key,
        sent_at=datetime.now(UTC),
        error=error,
    )
    store.insert_delivery(ev)
    return ev
