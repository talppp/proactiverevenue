"""
L5 - Team assignment.

Takes a RoutingPlan from L4 and resolves it to a concrete team_member,
honouring:
  - After-hours policy (per rules YAML)
  - OOO calendar entries (from 09_Advanced_Tests/calendars/<name>_calendar.json)
  - Overload threshold (active_thread_count in the same calendar JSON)
  - Capacity (max_open_threads from team config)

Falls back to plan.fallback_owner, then to Nathalie as last resort.
Writes the routing_decision row.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.logging_setup import get_logger
from app.router.rules import RoutingPlan, _is_after_hours
from app.store import RoutingDecision, Store

log = get_logger("L5_assign")

# ---------------------------------------------------------------------------
# Calendar integration (Phase 2 fix).
# Calendars live in 09_Advanced_Tests/calendars/<name>_calendar.json
# relative to the project root.  We load them lazily and cache in-process.
# ---------------------------------------------------------------------------
_CAL_CACHE: dict[str, list[dict]] = {}
_CAL_LOAD_ATTEMPTED: set[str] = set()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CAL_DIR = _PROJECT_ROOT / "09_Advanced_Tests" / "calendars"


def _load_calendar(name: str) -> list[dict]:
    """Load and cache calendar events for a team member."""
    key = name.lower()
    if key in _CAL_LOAD_ATTEMPTED:
        return _CAL_CACHE.get(key, [])
    _CAL_LOAD_ATTEMPTED.add(key)
    cal_path = _CAL_DIR / f"{key}_calendar.json"
    if not cal_path.exists():
        log.debug("calendar_not_found", name=name, path=str(cal_path))
        return []
    try:
        data = json.loads(cal_path.read_text(encoding="utf-8"))
        events = data if isinstance(data, list) else data.get("events", [])
        _CAL_CACHE[key] = events
        log.info("calendar_loaded", name=name, events=len(events))
        return events
    except Exception as exc:
        log.warning("calendar_load_error", name=name, error=str(exc))
        return []


def _is_ooo(name: str, at: datetime) -> bool:
    """Return True if the team member is marked OOO at the given datetime."""
    events = _load_calendar(name)
    at_utc = at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
    for ev in events:
        if ev.get("type") not in ("ooo", "out_of_office"):
            continue
        try:
            start = datetime.fromisoformat(str(ev["start"]).replace("Z", "+00:00"))
            end   = datetime.fromisoformat(str(ev["end"]).replace("Z", "+00:00"))
            if start <= at_utc < end:
                log.info("ooo_detected", name=name, event=ev.get("title", "OOO"))
                return True
        except Exception:
            continue
    return False


def _is_overloaded(name: str, at: datetime, max_threads: int) -> bool:
    """Return True if active_thread_count in the calendar exceeds max_threads."""
    events = _load_calendar(name)
    at_utc = at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
    for ev in events:
        if ev.get("type") != "overload":
            continue
        try:
            start = datetime.fromisoformat(str(ev["start"]).replace("Z", "+00:00"))
            end   = datetime.fromisoformat(str(ev["end"]).replace("Z", "+00:00"))
            if start <= at_utc < end:
                count     = int(ev.get("active_thread_count", 0))
                threshold = int(ev.get("threshold", max_threads))
                if count >= threshold:
                    log.info("overload_detected", name=name,
                             count=count, threshold=threshold)
                    return True
        except Exception:
            continue
    return False


def _member_available(name: str, row: dict[str, Any] | None,
                      after_hours: bool, received_at: datetime) -> bool:
    """Return True if the team member can accept this message right now."""
    if row is None:
        return False
    # After-hours gate
    if after_hours and not row.get("after_hours_ok"):
        log.info("skip_after_hours", name=name)
        return False
    # OOO gate
    if _is_ooo(name, received_at):
        return False
    # Overload gate
    max_t = row.get("max_open_threads", 99)
    if _is_overloaded(name, received_at, max_t):
        return False
    return True


def _pick_owner(plan: RoutingPlan, store: Store,
                received_at: datetime | None) -> str | None:
    """Choose the actual team-member name to assign.  None means archive."""
    if plan.is_archive:
        return None
    at = received_at or datetime.now(UTC)
    after_hours = _is_after_hours(received_at)
    primary  = plan.primary_owner
    fallback = plan.fallback_owner

    primary_row = store.team_member(primary) if primary else None
    if _member_available(primary or "", primary_row, after_hours, at):
        return primary

    # Fall through to fallback
    fb_row = store.team_member(fallback) if fallback else None
    if _member_available(fallback or "", fb_row, after_hours, at):
        return fallback

    # Last resort: Nathalie (after_hours_ok=true, always exists).
    nathalie_row = store.team_member("Nathalie")
    if nathalie_row:
        return "Nathalie"
    return None


def assign(*, message_id: str, classification_id: str,
           plan: RoutingPlan, store: Store,
           received_at: datetime | None = None) -> RoutingDecision:
    owner = _pick_owner(plan, store, received_at)
    decision = RoutingDecision(
        id=str(uuid.uuid4()),
        message_id=message_id,
        classification_id=classification_id,
        primary_owner=owner,
        fallback_owner=(plan.fallback_owner
                        if owner == plan.primary_owner
                        else plan.primary_owner),
        rule_id_matched=plan.rule_id_matched,
        reason=plan.reason,
        is_archive=plan.is_archive,
        is_unsure=plan.is_unsure,
    )
    store.insert_routing(decision)
    store.insert_audit(
        actor="system", action="L4_L5.routed",
        entity_kind="inbound_message", entity_id=message_id,
        payload={"owner": owner, "rule": plan.rule_id_matched,
                 "reason": plan.reason,
                 "is_archive": plan.is_archive,
                 "is_unsure": plan.is_unsure,
                 "deliver": plan.deliver},
    )
    log.info("assigned",
             message_id=message_id, owner=owner,
             rule=plan.rule_id_matched, is_unsure=plan.is_unsure)
    return decision
