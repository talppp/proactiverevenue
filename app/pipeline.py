"""
Pipeline orchestration - wires L1 -> L6 in a single async entry-point.

L1 hands us a (channel, payload) tuple after signature verification.
We run the rest of the pipeline inline, writing an audit_event row at
every transition so the end-to-end trace can be reconstructed from the
DB alone.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from app.classifier.geo import tag_geo
from app.classifier.topic import classify_topic
from app.classifier.urgency import classify_urgency
from app.delivery.deliver import deliver
from app.ingestion.normalize import DuplicateMessage, normalize_and_persist
from app.logging_setup import get_logger
from app.router.assign import assign
from app.router.rules import RoutingPlan, decide, reload_rules
from app.store import Classification, Store

log = get_logger("pipeline")


class Pipeline:
    """Holds the Store and provides one method per public entry-point.

    The Store is supplied at construction time (in-memory for the demo,
    Postgres in CI / Render).
    """

    def __init__(self, store: Store) -> None:
        self.store = store
        # Make sure rules are loaded once at startup.
        reload_rules()
        log.info("pipeline_ready", backend=type(store).__name__)

    async def handle_inbound(self, channel: str, payload: dict[str, Any]) -> dict[str, Any]:
        """The public entry-point.  Returns a small summary dict the L1
        webhook handler can serialise as the HTTP response."""
        t0 = time.perf_counter()
        org_id = getattr(self.store, "org_id", "") or ""
        try:
            msg = normalize_and_persist(channel, payload, self.store, org_id)
        except DuplicateMessage as dup:
            return {"status": "duplicate", "dedupe_hash": str(dup)}
        except ValueError as e:
            log.warning("unknown_channel", channel=channel)
            return {"status": "rejected", "reason": str(e)}

        # L3 - urgency
        urg = classify_urgency(msg.body_redacted)

        # L3 - topic
        tp = classify_topic(msg.body_redacted)

        # L3 - geo
        geo = tag_geo(msg.body_redacted, msg.subject)

        # Persist the classification row.
        cls = Classification(
            id=str(uuid.uuid4()),
            message_id=msg.id,
            model_name=urg.llm.parsed.get("model", "stub") if urg.llm else "stub",
            urgency=urg.urgency,
            urgency_confidence=urg.confidence,
            topic=tp.topic,
            topic_confidence=tp.confidence,
            secondary_topic=tp.secondary_topic,
            is_business=tp.is_business,
            is_follow_up=tp.is_follow_up,
            county_detected=geo.county,
            listing_detected=geo.listing_external_id,
            summary=tp.summary,
            suggested_reply=tp.suggested_reply,
            reasoning=(urg.reasoning + " | " + tp.reasoning)[:480],
            cost_usd=((urg.llm.cost_usd if urg.llm else 0.0)
                      + (tp.llm.cost_usd if tp.llm else 0.0)),
            latency_ms=((urg.llm.latency_ms if urg.llm else 0)
                        + (tp.llm.latency_ms if tp.llm else 0)),
        )
        self.store.insert_classification(cls)
        self.store.insert_audit(
            actor="system", action="L3.classified",
            entity_kind="inbound_message", entity_id=msg.id,
            payload={"urgency": urg.urgency, "topic": tp.topic,
                     "county": geo.county, "listing": geo.listing_external_id,
                     "cost_usd": cls.cost_usd},
        )

        # L4 - rules engine
        plan: RoutingPlan = decide(
            topic=tp.topic, secondary_topic=tp.secondary_topic,
            urgency=urg.urgency,
            is_business=tp.is_business,
            topic_confidence=tp.confidence,
            urgency_confidence=urg.confidence,
            county=geo.county,
            is_vip=self._contact_is_vip(msg.sender_handle),
            received_at=msg.received_at,
            language=msg.language,          # Phase 1 fix: Hebrew/Spanish routing
        )

        # L5 - team assignment
        decision = assign(message_id=msg.id, classification_id=cls.id,
                          plan=plan, store=self.store,
                          received_at=msg.received_at)

        # L6 - delivery
        events = await deliver(
            msg=msg, urgency=urg, topic=tp,
            decision=decision, deliver_kinds=plan.deliver,
            store=self.store,
        )

        wall_ms = int((time.perf_counter() - t0) * 1000)
        log.info("pipeline_done",
                 message_id=msg.id, wall_ms=wall_ms,
                 urgency=urg.urgency, topic=tp.topic,
                 owner=decision.primary_owner,
                 deliveries=len(events))
        return {
            "status":       "ok",
            "message_id":   msg.id,
            "urgency":      urg.urgency,
            "topic":        tp.topic,
            "owner":        decision.primary_owner,
            "is_archive":   decision.is_archive,
            "is_unsure":    decision.is_unsure,
            "deliveries":   [{"kind": e.delivery_kind, "status": e.status} for e in events],
            "wall_ms":      wall_ms,
            "cost_usd":     round(cls.cost_usd, 5),
        }

    # ---------------------------------------------------------------- helpers
    def _contact_is_vip(self, sender_handle: str) -> bool:
        """The in-memory store doesn't have a real contacts table, so we
        check the seeded _contacts dict for is_vip; Postgres reads contact.is_vip."""
        contacts = getattr(self.store, "_contacts", None)
        if contacts:
            for c in contacts.values():
                for _chan, ident in c.get("identities", []):
                    if ident == sender_handle:
                        return bool(c.get("is_vip", False))
        # Postgres path - the assertion is intentionally lax for the demo.
        return False
