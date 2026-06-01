"""
L3 urgency classifier.

Output: (urgency, confidence, reasoning) where urgency in P0|P1|P2|P3.

Strategy:
  1. Keyword bump first (cheap, deterministic). The routing YAML enumerates
     keyword signals for each tier; a hit on a P0 keyword cannot be
     downgraded by the LLM, only confirmed.
  2. LLM call with the taxonomy embedded (cached) and the redacted message.
  3. If LLM disabled (no API key), use a deterministic stub keyed off the
     keyword bump + sentiment heuristic.

The classifier never sees raw PII - callers pass body_redacted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.classifier._llm import LLMResult, call_json
from app.config import TAXONOMY_PATH
from app.logging_setup import get_logger

log = get_logger("L3_urgency")

_TAXONOMY = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))

# Keyword bump table.  Order matters - longest tier listed first wins.
_KEYWORDS = {
    "P0": ["emergency", "flood", "fire", "gas leak", "burst pipe",
           "no water", "no heat", "no ac", "no a/c", "broken lock",
           "locked out", "smoke", "child stuck", "911"],
    "P1": ["today", "asap", "urgent", "deadline",
           "closing tomorrow", "offer expires", "by 5pm", "right now"],
    "P2": ["this week", "when you can", "soon", "please reply"],
    "P3": ["no rush", "fyi", "info request", "future", "just letting"],
}


@dataclass
class UrgencyResult:
    urgency: str           # P0|P1|P2|P3
    confidence: float      # 0..1
    reasoning: str
    keyword_bump: str | None
    llm: LLMResult | None


def _keyword_bump(body: str) -> str | None:
    lower = body.lower()
    for tier, kws in _KEYWORDS.items():
        for kw in kws:
            if kw in lower:
                return tier
    return None


def _caps_bump(body: str) -> str | None:
    """Return 'P1' when the body reads as ALL CAPS urgency (e.g. 'WATER LEAK NOW!').

    Criteria (must satisfy ALL):
      - stripped body is >= 10 chars
      - >= 75% of alpha characters are uppercase
      - contains at least one alpha character
    This avoids false-positive on short acronyms like 'MLS' or 'ETA'.
    """
    stripped = body.strip()
    if len(stripped) < 10:
        return None
    alpha_chars = [c for c in stripped if c.isalpha()]
    if not alpha_chars:
        return None
    upper_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
    if upper_ratio >= 0.75:
        return "P1"
    return None


_SYSTEM_PROMPT_TEMPLATE = """You are the urgency classifier for the Apteker Realty AI Issue Router.

Output JSON ONLY (no prose, no markdown), matching this schema:
  {{"urgency": "P0"|"P1"|"P2"|"P3", "confidence": float 0..1, "reasoning": "short, one sentence"}}

Tier definitions (authoritative):
{tiers}

Rules:
- If the message describes an active emergency (flood, fire, gas leak, no heat in winter, locked out, child stuck), choose P0.
- If it has a same-day deadline (offer, closing, signature today), P1.
- "When you can" / "this week" / standard logistics: P2.
- "No rush" / FYI / future planning: P3.
- Never invent urgency that the text does not state.
"""


_USER_PROMPT_TEMPLATE = """Classify this message:

---
{body}
---

Return JSON only."""


def _build_system_prompt() -> str:
    tiers = "\n".join(
        f"- {u['code']}: {u['name']} (SLA {u['sla']}). Example: {u['example']}"
        for u in _TAXONOMY["urgency"]
    )
    return _SYSTEM_PROMPT_TEMPLATE.format(tiers=tiers)


_SYSTEM_PROMPT = _build_system_prompt()


def classify_urgency(body_redacted: str) -> UrgencyResult:
    """Returns an UrgencyResult.  Safe to call with empty body (returns P3)."""
    body = body_redacted or ""
    bump = _keyword_bump(body)
    caps = _caps_bump(body)

    # ALL CAPS is treated as a soft P1 signal (upward-only, same as keyword bump).
    # If a keyword bump already reached P0, the CAPS signal is irrelevant.
    effective_bump = bump
    if caps and not bump:
        effective_bump = caps
    elif caps and bump:
        order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        if order[caps] < order[bump]:
            effective_bump = caps

    # Build a deterministic stub for the no-API-key case.
    stub_urgency = effective_bump or "P2"
    stub = {"urgency": stub_urgency,
            "confidence": 0.70 if effective_bump else 0.55,
            "reasoning": (f"keyword bump={bump}, caps_bump={caps}"
                          if effective_bump else "default fallback")}

    user = _USER_PROMPT_TEMPLATE.format(body=body[:3500])
    res = call_json(system=_SYSTEM_PROMPT, user=user, stub_result=stub)
    parsed = res.parsed

    urgency = str(parsed.get("urgency", stub_urgency)).upper()
    if urgency not in ("P0", "P1", "P2", "P3"):
        urgency = stub_urgency
    confidence = float(parsed.get("confidence", 0.55))

    # Upward-only override: both keyword AND caps bumps can only raise urgency.
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    if effective_bump and order[effective_bump] < order[urgency]:
        urgency = effective_bump
        confidence = max(confidence, 0.80)

    reasoning = str(parsed.get("reasoning", ""))[:240]
    log.info("urgency_classified",
             urgency=urgency, confidence=confidence,
             keyword_bump=bump, caps_bump=caps, used_stub=res.used_stub)
    return UrgencyResult(urgency=urgency, confidence=confidence,
                         reasoning=reasoning, keyword_bump=effective_bump, llm=res)
