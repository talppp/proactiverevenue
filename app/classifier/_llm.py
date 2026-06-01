"""
Shared LLM helper.

Why centralize:
  - One place for prompt caching (50% cost discount, 80% latency cut on
    repeated taxonomy prompts).
  - One place for the token budget gate (4 000 input tokens hard cap).
  - One place for tenacity-based retry on 5xx.
  - One place for the daily cost ceiling.
  - One place that knows how to fall back to a deterministic stub when
    ANTHROPIC_API_KEY is unset (lets unit tests + the demo run offline).

The two production classifiers (urgency, topic) wrap call_json() with their
own prompts; geo uses a regex fast path and only falls back here.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from app.config import (
    LLM_DAILY_USD_CEILING,
    LLM_ENABLED,
    LLM_MAX_INPUT_TOKENS,
    LLM_MAX_OUTPUT_TOKENS,
    LLM_MODEL,
)
from app.logging_setup import get_logger

log = get_logger("L3_llm")


# ---------------------------------------------------------------------------
# Cost ledger (in-process; for the daily ceiling)
# ---------------------------------------------------------------------------
_USD_PER_MTOK_INPUT  = 1.00     # Haiku 4.5 published pricing
_USD_PER_MTOK_OUTPUT = 5.00
_USD_PER_MTOK_CACHE_READ = 0.10  # ~10x discount

_cost_by_day: dict[date, float] = defaultdict(float)


class CostCeilingExceeded(Exception):
    """Raised when today's spend would push past LLM_DAILY_USD_CEILING."""


class TokenBudgetExceeded(Exception):
    """Raised when prompt would exceed LLM_MAX_INPUT_TOKENS."""


@dataclass
class LLMResult:
    parsed: dict[str, Any]
    raw: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: int
    used_stub: bool


# ---------------------------------------------------------------------------
# Public entry-point.
# ---------------------------------------------------------------------------
def call_json(*, system: str, user: str,
              cache_system: bool = True,
              stub_result: dict[str, Any] | None = None) -> LLMResult:
    """Run a JSON-output LLM call.

    If LLM_ENABLED is false (no API key), returns *stub_result* wrapped in
    LLMResult.  Callers must always pass a usable stub so tests / demos
    work without an API key.
    """
    # Token budget check (rough: 4 chars per token).
    approx_tokens = (len(system) + len(user)) // 4
    if approx_tokens > LLM_MAX_INPUT_TOKENS:
        raise TokenBudgetExceeded(f"~{approx_tokens} tokens > limit {LLM_MAX_INPUT_TOKENS}")

    today = date.today()
    if _cost_by_day[today] >= LLM_DAILY_USD_CEILING:
        raise CostCeilingExceeded(f"day spend ${_cost_by_day[today]:.2f} >= ${LLM_DAILY_USD_CEILING}")

    if not LLM_ENABLED:
        if stub_result is None:
            raise RuntimeError("LLM disabled and no stub_result supplied")
        return LLMResult(parsed=stub_result, raw=json.dumps(stub_result),
                         prompt_tokens=0, completion_tokens=0,
                         cost_usd=0.0, latency_ms=0, used_stub=True)

    return _call_anthropic(system, user, cache_system)


# ---------------------------------------------------------------------------
# Real Anthropic call - kept behind tenacity retry + structured-output parse.
# ---------------------------------------------------------------------------
@retry(
    retry=retry_if_exception_type((TimeoutError, ConnectionError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=0.5, max=4.0),
    reraise=True,
)
def _call_anthropic(system: str, user: str, cache_system: bool) -> LLMResult:
    # Imported lazily so the rest of the codebase imports without anthropic installed.
    import anthropic
    client = anthropic.Anthropic()

    # System block - optionally cached.
    if cache_system:
        sys_block = [{
            "type": "text", "text": system,
            "cache_control": {"type": "ephemeral"},
        }]
    else:
        sys_block = system

    started = time.perf_counter()
    resp = client.messages.create(
        model=LLM_MODEL,
        max_tokens=LLM_MAX_OUTPUT_TOKENS,
        system=sys_block,
        messages=[{"role": "user", "content": user}],
    )
    latency_ms = int((time.perf_counter() - started) * 1000)

    # Concatenate text blocks (Haiku returns 1 in practice).
    raw = "".join(b.text for b in resp.content if hasattr(b, "text"))
    parsed = _safe_parse_json(raw)

    pt = resp.usage.input_tokens
    ct = resp.usage.output_tokens
    cache_read = getattr(resp.usage, "cache_read_input_tokens", 0)
    billable_input = pt - cache_read
    cost = (
        billable_input * _USD_PER_MTOK_INPUT / 1_000_000
        + cache_read * _USD_PER_MTOK_CACHE_READ / 1_000_000
        + ct * _USD_PER_MTOK_OUTPUT / 1_000_000
    )
    _cost_by_day[date.today()] += cost
    log.info("llm_call",
             model=LLM_MODEL,
             prompt_tokens=pt, cache_read=cache_read,
             completion_tokens=ct, cost_usd=round(cost, 5),
             latency_ms=latency_ms)
    return LLMResult(parsed=parsed, raw=raw, prompt_tokens=pt,
                     completion_tokens=ct, cost_usd=cost,
                     latency_ms=latency_ms, used_stub=False)


def _safe_parse_json(raw: str) -> dict[str, Any]:
    """Strip code fences if present, parse, return {} on failure (defensive)."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # Find the first {...} object in the text.
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            try:
                return json.loads(s[i:j+1])
            except json.JSONDecodeError:
                pass
        log.warning("llm_json_parse_failed", raw_head=s[:200])
        return {}
