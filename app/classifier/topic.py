"""
L3 topic classifier.

Two-stage:
  Stage A - is_business pre-filter.  Cheap structured-output call (or
            keyword fallback).  Non-business mail short-circuits with
            topic="personal"|"marketing_promo"|"spam".
  Stage B - full taxonomy classification when is_business=true.

Returns (topic, secondary_topic, confidence, is_business, is_follow_up,
summary, suggested_reply).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.classifier._llm import LLMResult, call_json
from app.config import TAXONOMY_PATH
from app.logging_setup import get_logger

log = get_logger("L3_topic")

_TAXONOMY = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
_VALID_TOPICS = {t["code"] for t in _TAXONOMY["topics"]}

# Quick heuristics for the no-API-key path.
# NOTE: "click here" removed — too broad; CRM tools use it in legitimate links.
_SPAM_HINTS = ("claim your prize", "you've won", "youve won",
               "crypto opportunity", "nigerian prince", "view this offer",
               "click here to claim", "unsubscribe from this list",
               "this is not spam", "verify your account now",
               "limited time offer", "act now to avoid")
_PROMO_HINTS = ("newsletter", "flash sale", "% off", "subscribe",
                "mailchimp", "constantcontact", "marketing")
_PERSONAL_HINTS = ("dinner", "yoga", "vet appointment", "school pickup",
                   "out of milk", "birthday cake", "mom's", "dentist")

# Languages that always route to Nathalie (per taxonomy open question T5 + Spanish fallback).
NATHALIE_LANGUAGES = frozenset({"he", "es"})


@dataclass
class TopicResult:
    topic: str
    secondary_topic: str | None
    confidence: float
    is_business: bool
    is_follow_up: bool       # filled by caller (needs DB lookup)
    summary: str
    suggested_reply: str
    reasoning: str
    llm: LLMResult | None


_TOPIC_SYSTEM = """You are the topic classifier for the Apteker Realty AI Issue Router.

Output JSON ONLY (no markdown), matching this schema:
  {{
    "is_business":     true|false,
    "topic":           one of {topics},
    "secondary_topic": one of {topics} or null,
    "confidence":      float 0..1,
    "summary":         "<= 160 chars, plain English, no quotes",
    "suggested_reply": "draft reply <= 320 chars (empty string if non-business)",
    "reasoning":       "one sentence"
  }}

Topic definitions (authoritative):
{defs}

Rules:
- "personal" = family / friends / non-real-estate.  is_business=false.
- "marketing_promo" = vendor cold pitches, newsletters.  is_business=false.
- "spam" = phishing / scams.  is_business=false.
- Everything else is business.
- secondary_topic only when a message clearly fits two business categories
  (e.g. buyer_lead + scheduling). Otherwise null.
- Do NOT invent properties, names, or facts.
"""


_TOPIC_USER = """Classify this message:

---
{body}
---

Return JSON only."""


def _build_system_prompt() -> str:
    topics_list = ", ".join(sorted(_VALID_TOPICS))
    defs = "\n".join(
        f"- {t['code']}: {t['label']}.  Example: {t['examples']}"
        for t in _TAXONOMY["topics"]
    )
    return _TOPIC_SYSTEM.format(topics=topics_list, defs=defs)


_SYSTEM_PROMPT = _build_system_prompt()


def _heuristic_topic(body: str) -> tuple[str, bool, float]:
    """Cheap fallback returning (topic, is_business, confidence)."""
    lower = body.lower()
    if any(h in lower for h in _SPAM_HINTS):
        return "spam", False, 0.85
    if any(h in lower for h in _PROMO_HINTS):
        return "marketing_promo", False, 0.75
    if any(h in lower for h in _PERSONAL_HINTS):
        return "personal", False, 0.70
    # Business hints
    if any(w in lower for w in ("flood", "leak", "no heat", "ac not", "locked out", "tenant")):
        return "tenant_issue", True, 0.65
    if any(w in lower for w in ("contract", "sign", "addendum", "redline")):
        return "contract", True, 0.65
    if "showing" in lower:
        return "showing", True, 0.65
    if any(w in lower for w in ("offer", "closing", "counter")):
        return "deal", True, 0.65
    if any(w in lower for w in ("vendor", "plumber", "painter", "electrician")):
        return "maintenance", True, 0.60
    if any(w in lower for w in ("listing", "selling my", "sell my")):
        return "seller_lead", True, 0.60
    if any(w in lower for w in ("looking for", "interested in", "viewing")):
        return "buyer_lead", True, 0.60
    return "unknown", True, 0.50


def classify_topic(body_redacted: str) -> TopicResult:
    body = body_redacted or ""
    stub_topic, stub_business, stub_conf = _heuristic_topic(body)

    summary_seed = body.strip().split("\n")[0][:160]
    stub = {
        "is_business":     stub_business,
        "topic":           stub_topic,
        "secondary_topic": None,
        "confidence":      stub_conf,
        "summary":         summary_seed,
        "suggested_reply": "" if not stub_business else
            "Thanks - I will look into this and get back to you shortly.",
        "reasoning":       "heuristic fallback (no LLM key)",
    }

    user = _TOPIC_USER.format(body=body[:3500])
    res = call_json(system=_SYSTEM_PROMPT, user=user, stub_result=stub)
    p = res.parsed

    topic = str(p.get("topic", stub_topic))
    if topic not in _VALID_TOPICS:
        log.warning("invalid_topic_returned", returned=topic, fallback=stub_topic)
        topic = stub_topic
    secondary = p.get("secondary_topic")
    if secondary and secondary not in _VALID_TOPICS:
        secondary = None
    confidence = float(p.get("confidence", stub_conf))
    is_business = bool(p.get("is_business", stub_business))
    # Force non-business topics
    if topic in ("personal", "marketing_promo", "spam"):
        is_business = False
    summary = str(p.get("summary", summary_seed))[:200]
    suggested = str(p.get("suggested_reply", ""))[:400]
    reasoning = str(p.get("reasoning", ""))[:240]

    log.info("topic_classified",
             topic=topic, secondary_topic=secondary,
             is_business=is_business, confidence=confidence,
             used_stub=res.used_stub)
    return TopicResult(
        topic=topic, secondary_topic=secondary, confidence=confidence,
        is_business=is_business, is_follow_up=False,
        summary=summary, suggested_reply=suggested,
        reasoning=reasoning, llm=res,
    )
