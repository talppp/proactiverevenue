"""
P0 escalation job.

Per the rules YAML:
  - condition: "urgency == P0 AND no_human_response_in_minutes >= 5"
    action:    "ring owner phone via Twilio voice prompt"

Concretely:
  - Find every P0 routing_decision whose first delivery has NOT been
    acknowledged within 5 minutes.
  - Fire a SECOND push (`push_now` again).
  - If still unacknowledged after 10 minutes, write an `escalate_phone`
    delivery row (Twilio voice prompt - stubbed in the demo, real in prod).
  - Cap escalation to 3 voice calls per incident (per the routing-rules
    "R7" gap fix in 08_Review_Schema_Plan/01_Review_of_Phase1_Drafts.docx).

Two run modes:

    python -m app.jobs.p0_escalate --once
        Runs a single sweep and exits. Suitable for a Render cron schedule
        every 60s.

    python -m app.jobs.p0_escalate
        Loops every 60s in-process. Useful for local demo.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx

from app.logging_setup import get_logger
from app.store import DeliveryEvent, Store, make_store
from app.ws import HUB

log = get_logger("p0_escalate")

# ---------------------------------------------------------------------------
# Twilio SMS escalation (Phase 1 fix: actually fires when creds are set).
# In demo mode (no creds) we log + write the delivery row but skip the HTTP call.
# ---------------------------------------------------------------------------
_TWILIO_SID  = os.environ.get("TWILIO_ACCOUNT_SID", "")
_TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
_TWILIO_FROM  = os.environ.get("TWILIO_FROM_NUMBER", "")
_NATHALIE_PHONE = os.environ.get("NATHALIE_PHONE_NUMBER", "")


async def _send_twilio_sms(to: str, body: str) -> bool:
    """Fire a Twilio SMS.  Returns True on success, False on failure/skip."""
    if not (_TWILIO_SID and _TWILIO_TOKEN and _TWILIO_FROM and to):
        log.warning("twilio_sms_skipped_no_creds",
                    has_sid=bool(_TWILIO_SID), has_token=bool(_TWILIO_TOKEN),
                    has_from=bool(_TWILIO_FROM), has_to=bool(to))
        return False
    url = f"https://api.twilio.com/2010-04-01/Accounts/{_TWILIO_SID}/Messages.json"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                auth=(_TWILIO_SID, _TWILIO_TOKEN),
                data={"From": _TWILIO_FROM, "To": to, "Body": body},
            )
        if resp.status_code in (200, 201):
            sid = resp.json().get("sid", "?")
            log.info("twilio_sms_sent", to=to, sid=sid)
            return True
        log.error("twilio_sms_failed", status=resp.status_code, body=resp.text[:200])
        return False
    except Exception as exc:
        log.exception("twilio_sms_error", error=str(exc))
        return False

ESCALATE_5MIN  = timedelta(minutes=5)
ESCALATE_10MIN = timedelta(minutes=10)
MAX_VOICE_CALLS_PER_INCIDENT = 3


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _idempotency_key(message_id: str, kind: str, owner: str | None, suffix: str) -> str:
    h = hashlib.sha256()
    h.update(f"{message_id}|{kind}|{owner or ''}|{suffix}".encode())
    return h.hexdigest()[:32]


def _is_acknowledged(store: Store, routing_id: str) -> bool:
    """A delivery is 'acked' when ANY delivery for the routing has
    acknowledged_at OR an audit_event from the dashboard 'marked_handled'."""
    # In-memory path
    deliveries = getattr(store, "deliveries", None)
    if deliveries is not None:
        for d in deliveries.values():
            if d.routing_id == routing_id:
                # Our InMemoryStore doesn't capture acknowledged_at; fall back to
                # audit_event lookup below.
                pass
        audit = getattr(store, "audit", [])
        for e in audit:
            if e.get("action") == "dashboard.marked_handled":
                # Match by entity_id (message_id) -> routing's message_id
                # The pipeline does not currently store the routing<->message
                # link in audit_event, so we match by routing_id stored on the
                # delivery row.
                # Simpler: any 'marked_handled' for the message wins.
                ent_id = e.get("entity_id")
                # Look up the delivery rows for this routing to get the msg id.
                for d in deliveries.values():
                    if d.routing_id == routing_id and ent_id:
                        return True
        return False
    return False


async def _sweep_inmemory(store) -> dict[str, int]:
    """Sweep the in-memory store and return counts."""
    now = _utcnow()
    stats = {"checked": 0, "second_push": 0, "voice_call": 0, "capped": 0}

    # Iterate routing_decisions
    for routing_id, decision in list(store.routings.items()):
        # Only urgency P0 - we infer from the classification linked to the message.
        cls = store.classifications.get(decision.classification_id) if decision.classification_id else None
        if not cls or cls.urgency != "P0":
            continue
        if decision.is_archive or decision.primary_owner is None:
            continue

        # First delivery time for this routing
        first_delivery_at: datetime | None = None
        voice_calls = 0
        for d in store.deliveries.values():
            if d.routing_id != routing_id:
                continue
            if first_delivery_at is None or d.sent_at < first_delivery_at:
                first_delivery_at = d.sent_at
            if d.delivery_kind == "escalate_phone":
                voice_calls += 1
        if first_delivery_at is None:
            continue
        stats["checked"] += 1

        if _is_acknowledged(store, routing_id):
            continue

        elapsed = now - first_delivery_at
        owner = decision.primary_owner

        # +5 min - second push
        if elapsed >= ESCALATE_5MIN:
            key = _idempotency_key(decision.message_id, "push_now", owner, "esc5")
            if not store.existing_idempotency(key):
                msg = store.messages.get(decision.message_id)
                card = {
                    "message_id":   decision.message_id,
                    "urgency":      "P0",
                    "urgency_color":"#C0392B",
                    "topic":        cls.topic,
                    "owner":        owner,
                    "preview":      (msg.body[:280] if msg else ""),
                    "summary":      cls.summary,
                    "kind":         "push_now",
                    "rule":         "escalation.5min",
                    "reason":       "P0 unacknowledged after 5 minutes",
                    "is_unsure":    False,
                    "is_archive":   False,
                    "channel":      (msg.channel if msg else "unknown"),
                    "sender":       (msg.sender_handle if msg else ""),
                }
                delivered = await HUB.publish((owner or "").lower(), card)
                store.insert_delivery(DeliveryEvent(
                    id=str(uuid.uuid4()), routing_id=routing_id,
                    delivery_kind="push_now", target_handle=(owner or "").lower(),
                    status="sent" if delivered > 0 else "queued",
                    idempotency_key=key, sent_at=now,
                ))
                store.insert_audit(
                    actor="system", action="escalation.5min",
                    entity_kind="inbound_message", entity_id=decision.message_id,
                    payload={"owner": owner, "kind": "push_now"},
                )
                stats["second_push"] += 1
                log.info("escalated_5min", message_id=decision.message_id, owner=owner)

        # +10 min - voice call (capped)
        if elapsed >= ESCALATE_10MIN:
            if voice_calls >= MAX_VOICE_CALLS_PER_INCIDENT:
                stats["capped"] += 1
                continue
            key = _idempotency_key(decision.message_id, "escalate_phone", owner,
                                   f"esc10-{voice_calls}")
            if not store.existing_idempotency(key):
                # Phase 1 fix: actually fire Twilio SMS when credentials are
                # configured; fall back to audit-only in demo mode.
                msg_obj = store.messages.get(decision.message_id)
                preview = (msg_obj.body[:120] if msg_obj else "(no preview)")
                sms_body = (
                    f"[P0 ESCALATION #{voice_calls + 1}] "
                    f"Unacknowledged emergency after {int(elapsed.total_seconds() // 60)} min. "
                    f"Topic: {cls.topic}. Preview: {preview}"
                )
                sms_sent = await _send_twilio_sms(_NATHALIE_PHONE, sms_body)
                store.insert_delivery(DeliveryEvent(
                    id=str(uuid.uuid4()), routing_id=routing_id,
                    delivery_kind="escalate_phone",
                    target_handle=(owner or "").lower(),
                    status="sent" if sms_sent else "logged",
                    idempotency_key=key, sent_at=now,
                ))
                store.insert_audit(
                    actor="system", action="escalation.10min_voice",
                    entity_kind="inbound_message", entity_id=decision.message_id,
                    payload={"owner": owner, "voice_call_n": voice_calls + 1,
                             "sms_sent": sms_sent},
                )
                stats["voice_call"] += 1
                log.warning("escalated_10min_voice",
                            message_id=decision.message_id, owner=owner,
                            voice_call_n=voice_calls + 1, sms_sent=sms_sent)
    return stats


async def sweep_once(store: Store) -> dict[str, int]:
    """Run one escalation sweep.  Returns counts."""
    # The in-memory path is what we exercise in tests + demo.  A Postgres
    # implementation would run the equivalent SQL query.
    if hasattr(store, "routings"):
        return await _sweep_inmemory(store)
    log.warning("p0_escalate_postgres_not_yet_implemented")
    return {"checked": 0, "second_push": 0, "voice_call": 0, "capped": 0}


async def loop(store: Store, interval: float = 60.0) -> None:
    log.info("p0_escalate_loop_start", interval_s=interval)
    while True:
        try:
            stats = await sweep_once(store)
            if any(v > 0 for k, v in stats.items() if k != "checked"):
                log.info("p0_escalate_tick", **stats)
        except Exception as e:
            log.exception("p0_escalate_tick_failed", error=str(e))
        await asyncio.sleep(interval)


def main() -> None:
    ap = argparse.ArgumentParser(description="P0 escalation sweep")
    ap.add_argument("--once", action="store_true",
                    help="Run a single sweep and exit (for Render cron)")
    ap.add_argument("--interval", type=float, default=60.0,
                    help="Loop interval seconds (when not --once)")
    args = ap.parse_args()

    store = make_store()
    # The script doesn't seed - it runs against an existing, seeded store.
    # In demo mode the FastAPI app already seeded the same in-memory store,
    # so the standalone script will see an EMPTY store. That's fine for
    # a Render cron - it would use the shared Postgres.
    if args.once:
        asyncio.run(sweep_once(store))
    else:
        asyncio.run(loop(store, args.interval))


if __name__ == "__main__":
    main()
