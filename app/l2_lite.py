"""L2-lite: a deployable thinking layer for the L1 service.

Until the full Drive-project pipeline (classifier/router/delivery with
LLM fallback, geo, VIP lookup) is merged into this repo, this module
gives the deployed service real — if modest — intelligence:

  - rule-based urgency (P0-P3) and topic classification, keyword tables
    lifted from 04_Rules_and_Routing/routing_rules.yaml
  - contact memory: per-sender history from sms_inbox_raw, so repeat
    texters carry thread context instead of being classified in amnesia
  - owner routing from the same routing rules (topic -> nathalie/fabi/noa)
  - a BudgetGuard that enforces LLM_DAILY_USD_CEILING at runtime; the
    optional LLM fallback for low-confidence messages is refused once
    the day's budget is spent
  - every result carries confidences and an is_unsure flag so
    low-confidence routing is visible, not silent

The LLM fallback is injected as a callable so tests never hit the
network; when ANTHROPIC_API_KEY is unset the rule-based path is final.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from app import config
from app.logging_setup import get_logger
from app.sms_inbox_store import SmsInboxStore

log = get_logger("l2_lite")

# --- urgency keywords (routing_rules.yaml: urgency_rules) ----------------
_URGENCY_RULES: list[tuple[str, list[str]]] = [
    ("P0", ["emergency", "flood", "fire", "gas leak", "no water", "broken lock",
            "locked out", "burst pipe", "no heat", "no ac", "smoke", "child stuck"]),
    ("P1", ["today", "asap", "urgent", "deadline", "closing tomorrow",
            "offer expires", "right away", "immediately"]),
    ("P3", ["no rush", "fyi", "info request", "whenever", "future", "next month"]),
    # P2 is the default; "this week"/"soon" map there anyway.
]

# --- topic keywords (routing_rules.yaml: topic_routing) -------------------
_TOPIC_RULES: list[tuple[str, list[str]]] = [
    ("showing",      ["showing", "tour", "open house", "see the house", "see the property"]),
    ("seller_lead",  ["sell my", "sell our", "list my", "list our", "listing my", "thinking of selling"]),
    ("buyer_lead",   ["interested in", "looking to buy", "saw the listing", "your listing", "move asap"]),
    ("maintenance",  ["leak", "repair", "broken", "faucet", "no hot water", "ac stopped",
                      "heater", "burst pipe", "flood", "no heat", "no ac", "gas leak"]),
    ("tenant_issue", ["tenant", "apartment", "lease", "unit "]),
    ("contract",     ["contract", "signature", "sign the", "docusign", "renew the"]),
    ("document",     ["document", "paperwork", "proof of", "upload"]),
    ("billing",      ["payment", "invoice", "deposit", "late fee", "billing", "refund"]),
    ("scheduling",   ["reschedule", "schedule", "appointment", "move the showing", "time change"]),
    ("deal",         ["offer", "closing", "appraisal", "escrow", "counteroffer", "under contract"]),
    ("vendor",       ["vendor", "plumber", "electrician", "contractor", "landscap"]),
]

# topic -> (primary_owner, fallback) per routing_rules.yaml topic_routing
_OWNER_MAP: dict[str, tuple[str, str]] = {
    "buyer_lead":   ("nathalie", "fabi"),
    "seller_lead":  ("nathalie", "fabi"),
    "showing":      ("nathalie", "fabi"),
    "tenant_issue": ("nathalie", "noa"),
    "maintenance":  ("noa", "fabi"),
    "contract":     ("noa", "nathalie"),
    "document":     ("noa", "nathalie"),
    "vendor":       ("noa", "nathalie"),
    "admin":        ("noa", "nathalie"),
    "scheduling":   ("noa", "nathalie"),
    "billing":      ("noa", "nathalie"),
    "deal":         ("nathalie", "noa"),
    "vip_client":   ("nathalie", "nathalie"),
    "unknown":      ("nathalie", "nathalie"),  # confidence floor -> owner reviews
}


def _keyword_hits(body_lower: str, keywords: list[str]) -> int:
    return sum(1 for kw in keywords if kw in body_lower)


def classify_urgency(body: str) -> tuple[str, float]:
    """Returns (urgency, confidence). P2 is the default lane; keyword
    matches only ever bump urgency up or down explicitly, mirroring the
    full pipeline's 'bumps go upward' rule for P0."""
    body_lower = body.lower()
    for level, keywords in _URGENCY_RULES:
        hits = _keyword_hits(body_lower, keywords)
        if hits:
            return level, min(0.6 + 0.15 * hits, 0.95)
    return "P2", 0.6


def classify_topic(body: str) -> tuple[str, float]:
    """Returns (topic, confidence). Best keyword-table match wins; ties
    break by table order (most specific first). No match -> 'unknown'
    at 0.3, which lands below CONFIDENCE_FLOOR and flags is_unsure."""
    body_lower = body.lower()
    best: tuple[str, int] = ("unknown", 0)
    for topic, keywords in _TOPIC_RULES:
        hits = _keyword_hits(body_lower, keywords)
        if hits > best[1]:
            best = (topic, hits)
    if best[1] == 0:
        return "unknown", 0.3
    return best[0], min(0.55 + 0.15 * best[1], 0.95)


def route_owner(topic: str, urgency: str) -> tuple[str, str]:
    """Returns (primary_owner, fallback_owner). P0 always lands on the
    after-hours-ok owner (Nathalie) regardless of topic."""
    if urgency == "P0":
        return "nathalie", _OWNER_MAP.get(topic, _OWNER_MAP["unknown"])[0]
    return _OWNER_MAP.get(topic, _OWNER_MAP["unknown"])


class BudgetGuard:
    """Runtime enforcement of LLM_DAILY_USD_CEILING.

    The ceiling was previously configuration-only; nothing refused a
    call once the budget was gone. This makes the refusal real and
    persists spend in the store so it survives restarts (Postgres) and
    is shared across workers."""

    def __init__(self, store: SmsInboxStore, daily_ceiling_usd: float) -> None:
        self.store = store
        self.ceiling = daily_ceiling_usd

    def can_spend(self, usd: float) -> bool:
        try:
            return self.store.llm_spend_today() + usd <= self.ceiling
        except Exception as exc:  # noqa: BLE001 - budget check must not kill classify
            log.warning("budget_check_failed_failing_closed", error=repr(exc))
            return False  # fail closed: no budget visibility -> no LLM spend

    def record(self, usd: float) -> float:
        try:
            return self.store.add_llm_spend(usd)
        except Exception as exc:  # noqa: BLE001
            log.warning("budget_record_failed", error=repr(exc))
            return -1.0


# An LLM classify function takes (body, context_bodies) and returns
# (urgency, topic, confidence, cost_usd). Injected so tests never hit
# the network and the rule path stays deterministic.
LlmClassifyFn = Callable[[str, list[str]], tuple[str, str, float, float]]


class LitePipeline:
    """Drop-in for LoggingPipeline: same handle_inbound contract, but the
    result now carries classification, routing, thread context, and
    budget state instead of an empty ack."""

    def __init__(
        self,
        store: SmsInboxStore,
        llm_fn: LlmClassifyFn | None = None,
        confidence_floor: float | None = None,
        daily_ceiling_usd: float | None = None,
    ) -> None:
        self.store = store
        self.llm_fn = llm_fn
        self.floor = confidence_floor if confidence_floor is not None else config.CONFIDENCE_FLOOR
        self.budget = BudgetGuard(
            store,
            daily_ceiling_usd if daily_ceiling_usd is not None else config.LLM_DAILY_USD_CEILING,
        )
        self.received: list[tuple[str, dict[str, Any]]] = []  # test/debug trace

    async def handle_inbound(self, channel: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.received.append((channel, payload))
        body = payload.get("body", "")
        from_number = payload.get("from_number", "")
        msg_id = payload.get("msg_id", "")

        # Contact memory: thread context for this sender (excludes nothing —
        # the current row may already be staged, which is fine; depth >= 1
        # for a repeat sender is the signal that matters).
        try:
            history = self.store.recent_by_sender(from_number, limit=6)
        except Exception as exc:  # noqa: BLE001 - memory is enrichment, not a gate
            log.warning("contact_memory_unavailable", error=repr(exc))
            history = []
        prior = [r for r in history if r.msg_id != msg_id]

        urgency, uconf = classify_urgency(body)
        topic, tconf = classify_topic(body)
        used_llm = False

        # LLM fallback only when rules are unsure AND budget remains.
        est_cost = 0.002  # conservative haiku-class estimate per classify
        if (
            min(uconf, tconf) < self.floor
            and self.llm_fn is not None
            and self.budget.can_spend(est_cost)
        ):
            try:
                urgency, topic, llm_conf, cost = self.llm_fn(
                    body, [r.body for r in prior]
                )
                uconf = tconf = llm_conf
                self.budget.record(cost)
                used_llm = True
            except Exception as exc:  # noqa: BLE001 - fall back to rule result
                log.warning("llm_fallback_failed", error=repr(exc))

        is_unsure = min(uconf, tconf) < self.floor
        primary, fallback = route_owner(topic, urgency)

        log.info(
            "l2_lite_classified",
            channel=channel,
            msg_id=msg_id,
            urgency=urgency,
            topic=topic,
            owner=primary,
            unsure=is_unsure,
            thread_depth=len(prior),
            used_llm=used_llm,
        )
        return {
            "status": "ok",
            "message_id": msg_id,
            "urgency": urgency,
            "urgency_confidence": round(uconf, 2),
            "topic": topic,
            "topic_confidence": round(tconf, 2),
            "owner": primary,
            "fallback_owner": fallback,
            "is_unsure": is_unsure,
            "thread_depth": len(prior),
            "used_llm": used_llm,
            "deliveries": [],
        }


def make_default_llm_fn() -> LlmClassifyFn | None:
    """Build the real Anthropic-backed fallback when a key is configured.
    Returns None otherwise, which disables the LLM path entirely."""
    if not config.ANTHROPIC_API_KEY:
        return None

    def _llm_classify(body: str, context: list[str]) -> tuple[str, str, float, float]:
        import json

        import httpx

        ctx = "\n".join(f"- {c[:200]}" for c in context[:5]) or "(none)"
        prompt = (
            "Classify this real-estate SMS. Reply with ONLY JSON: "
            '{"urgency":"P0|P1|P2|P3","topic":"buyer_lead|seller_lead|showing|'
            'tenant_issue|maintenance|contract|document|vendor|admin|scheduling|'
            'billing|deal|unknown","confidence":0.0-1.0}\n\n'
            f"Recent messages from this sender:\n{ctx}\n\nMessage:\n{body[:1000]}"
        )
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": config.LLM_MODEL,
                "max_tokens": config.LLM_MAX_OUTPUT_TOKENS,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"]
        parsed = json.loads(re.search(r"\{.*\}", text, re.DOTALL).group(0))
        usage = data.get("usage", {})
        # haiku-class pricing approximation; exact ledger lives in Phase 3
        cost = (usage.get("input_tokens", 0) * 1e-6) + (usage.get("output_tokens", 0) * 5e-6)
        return (
            parsed.get("urgency", "P2"),
            parsed.get("topic", "unknown"),
            float(parsed.get("confidence", 0.7)),
            cost,
        )

    return _llm_classify
