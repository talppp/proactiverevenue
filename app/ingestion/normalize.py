"""
L2 - Unified ingestion / normalization.

Every channel webhook flows through normalize() which:
  - converts the channel-specific payload into a canonical CanonicalMessage
  - detects language (heuristic; defaults to 'en')
  - redacts phone / email PII so the LLM never sees raw values
  - pre-processes voice transcripts (strips disfluencies / inaudibles)
  - computes a 60-second dedupe hash (channel-specific + cross-channel)
  - persists the row via the Store interface

This is the only place channel-specific quirks should live. Downstream
layers (L3-L6) consume CanonicalMessage and have no notion of channel
payload shape.
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.logging_setup import get_logger
from app.store import InboundMessage, Store, sha1_dedupe

log = get_logger("L2_normalize")

# ---------------------------------------------------------------------------
# PII redaction regex set.  Patterns chosen to be conservative (no false
# negatives on common formats), tolerant of one-off variations.
# ---------------------------------------------------------------------------
_PHONE_RE = re.compile(r"""
    (?:\+?1[\s\-.])?              # optional country code
    \(?\d{3}\)?[\s\-.]?           # area code
    \d{3}[\s\-.]?\d{4}             # local
""", re.VERBOSE)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_MLS_RE   = re.compile(r"\bMLS[-_ ]?(\d{6,8})\b", re.IGNORECASE)

def redact_pii(text: str) -> str:
    """Return a copy of *text* with phone numbers and emails replaced
    by stable placeholders. The unredacted text remains in inbound_message.body."""
    if not text:
        return ""
    out = _PHONE_RE.sub("<PHONE>", text)
    out = _EMAIL_RE.sub("<EMAIL>", out)
    return out

def detect_language(text: str) -> str:
    """Lightweight heuristic language detector.  Returns ISO 639-1.

    Priority order:
      'he' – any Hebrew Unicode characters (U+0590..U+05FF).
      'es' – Spanish-specific keyword heuristic (common function words).
      'en' – default.

    Sufficient for routing decisions (Hebrew → Nathalie, Spanish → Nathalie).
    """
    if not text:
        return "en"
    # Hebrew: Unicode block U+0590–U+05FF
    if any('֐' <= c <= '׿' for c in text):
        return "he"
    # Spanish heuristic: presence of 3+ Spanish-specific function words / diacritics.
    _SPANISH_TOKENS = (
        " que ", " con ", " para ", " por ", " una ", " del ", " los ",
        " las ", " está ", " esto ", " como ", " pero ", " hola ", " gracias ",
        "ción", "ñ", "¿", "¡",
    )
    lower = text.lower()
    hits = sum(1 for t in _SPANISH_TOKENS if t in lower)
    if hits >= 2:
        return "es"
    return "en"

# ---------------------------------------------------------------------------
# Voice transcript pre-processing
# ---------------------------------------------------------------------------
_VOICE_NOISE = re.compile(
    r"\[inaudible\]|\[crosstalk\]|\[noise\]|\[laughter\]|\[pause\]"
    r"|\[unclear\]|\(inaudible\)|\(crosstalk\)"
    r"|\bum+\b|\buh+\b|\buh-huh\b|\bhm+\b|\bmm+\b|\ber+\b",
    re.IGNORECASE,
)
# Channels that deliver voice transcripts.
_VOICE_CHANNELS = {"voice", "voicemail", "voice_message"}

def preprocess_voice(body: str, channel: str) -> str:
    """Strip disfluencies and transcript artifacts from voice channels.
    Returns the original body unchanged for non-voice channels."""
    if channel not in _VOICE_CHANNELS:
        return body
    cleaned = _VOICE_NOISE.sub(" ", body)
    # Collapse multiple spaces created by removals.
    cleaned = re.sub(r" {2,}", " ", cleaned).strip()
    return cleaned

# ---------------------------------------------------------------------------
# Per-channel canonicalisers.  Each returns a kwargs dict for InboundMessage.
# ---------------------------------------------------------------------------
def _from_sms(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "channel": "sms",
        "provider_msg_id": payload.get("msg_id") or payload.get("MessageSid"),
        "sender_handle": payload.get("sender_phone") or payload.get("From", ""),
        "subject": None,
        "body": payload.get("body") or payload.get("Body", ""),
    }

def _from_whatsapp(payload: dict[str, Any]) -> dict[str, Any]:
    body = payload.get("body") or payload.get("text", {}).get("body", "")
    return {
        "channel": "whatsapp",
        "provider_msg_id": payload.get("msg_id") or payload.get("wa_id"),
        "sender_handle": payload.get("sender_phone") or payload.get("from", ""),
        "subject": None,
        "body": body,
    }

def _from_email(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider_msg_id": payload.get("msg_id") or payload.get("id"),
        "sender_handle": payload.get("sender_email") or payload.get("from", ""),
        "subject": payload.get("subject"),
        "body": payload.get("body") or payload.get("plain", ""),
    }

_CHANNEL_FNS = {
    "sms":         _from_sms,
    "whatsapp":    _from_whatsapp,
    "email":       _from_email,
    "apple_mail":  _from_email,
}

# ---------------------------------------------------------------------------
# Public entry-point.
# ---------------------------------------------------------------------------
class DuplicateMessage(Exception):
    """Raised when a message with the same dedupe hash already exists within
    the 60-second window.  Caller should respond 200 OK and skip the pipeline."""

def normalize_and_persist(channel: str, payload: dict[str, Any],
                          store: Store, org_id: str) -> InboundMessage:
    """The single ingestion entry-point. Returns the persisted InboundMessage.

    Raises DuplicateMessage if a same-content message was seen recently.

    Deduplication strategy:
      1. Channel-specific hash: sha1(channel + sender + body[:200])
         - Anonymous senders ("unknown") include a 60-second time bucket so
           that truly identical messages from unknown senders within the same
           window are deduped, but messages in DIFFERENT windows are not.
      2. Cross-channel hash: sha1("any" + sender + body[:200])
         - Catches the same message arriving on both SMS and WhatsApp within
           60 seconds when a real sender identifier is known.
    """
    fn = _CHANNEL_FNS.get(channel)
    if not fn:
        raise ValueError(f"unknown channel: {channel!r}")
    base = fn(payload)

    # --- Voice transcript cleanup (Phase 2) ---------------------------------
    raw_body = base["body"] or ""
    body = preprocess_voice(raw_body, channel)
    if body != raw_body:
        log.debug("voice_transcript_cleaned", removed_chars=len(raw_body) - len(body))

    sender = base["sender_handle"] or "unknown"
    received_at = _parse_ts(payload.get("received_at"))

    # --- Deduplication ------------------------------------------------------
    # When sender is unknown, include a 60-second bucket so different
    # anonymous users in different time windows are never conflated.
    if sender == "unknown":
        bucket = int(received_at.timestamp() // 60)
        dedupe_sender = f"unknown:{bucket}"
    else:
        dedupe_sender = sender

    # Primary (channel-scoped) hash.
    dedupe = sha1_dedupe(base["channel"], dedupe_sender, body)
    if store.existing_dedupe(dedupe):
        log.info("dedup_hit", channel=base["channel"], sender=sender,
                 dedupe_hash=dedupe[:10], scope="channel")
        raise DuplicateMessage(dedupe)

    # Secondary (cross-channel) hash — only when we have a real sender handle.
    if sender != "unknown":
        xch_dedupe = sha1_dedupe("any", sender, body)
        if store.existing_dedupe(xch_dedupe):
            log.info("dedup_hit", channel=base["channel"], sender=sender,
                     dedupe_hash=xch_dedupe[:10], scope="cross_channel")
            raise DuplicateMessage(xch_dedupe)
    else:
        xch_dedupe = dedupe  # reuse; no meaningful cross-channel check for anon

    # Auto-generate provider_msg_id when the upstream didn't supply one.
    provider_msg_id = base["provider_msg_id"] or f"gen:{str(uuid.uuid4())}"

    msg = InboundMessage(
        id=str(uuid.uuid4()),
        org_id=org_id,
        channel=base["channel"],
        provider_msg_id=provider_msg_id,
        sender_handle=sender,
        subject=base["subject"],
        body=body,
        body_redacted=redact_pii(body),
        language=detect_language(body),
        received_at=received_at,
        dedupe_hash=dedupe,
        raw_payload=payload,
    )
    store.insert_message(msg)
    store.insert_audit(
        actor="system", action="L2.normalized",
        entity_kind="inbound_message", entity_id=msg.id,
        payload={"channel": msg.channel, "language": msg.language,
                 "redacted_len": len(msg.body_redacted),
                 "voice_cleaned": body != raw_body},
    )
    log.info("normalized", message_id=msg.id, channel=msg.channel,
             language=msg.language, body_len=len(body))
    return msg

def _parse_ts(v: Any) -> datetime:
    """Accept ISO string, datetime, or None - return aware UTC datetime."""
    if v is None:
        return datetime.now(UTC)
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return datetime.now(UTC)
