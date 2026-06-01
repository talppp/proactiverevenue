"""L2 normalization unit tests - PII redaction, dedupe hash, channel canonicalisation."""
import pytest

from app.ingestion.normalize import (
    DuplicateMessage,
    detect_language,
    normalize_and_persist,
    redact_pii,
)

# -- PII redaction -----------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Call me at (404) 555-1234 today",  "Call me at <PHONE> today"),
    ("Email john@gmail.com please",       "Email <EMAIL> please"),
    ("+1-404-555-1234 and mary@x.org",    "<PHONE> and <EMAIL>"),
    ("",                                  ""),
    ("No PII here at all",                "No PII here at all"),
])
def test_redact_pii(text, expected):
    assert redact_pii(text) == expected


# -- Language detection ------------------------------------------------------

def test_detect_language_english():
    assert detect_language("Hello world") == "en"


def test_detect_language_hebrew():
    assert detect_language("שלום עולם") == "he"


def test_detect_language_empty():
    assert detect_language("") == "en"


# -- normalize_and_persist ---------------------------------------------------

def test_normalize_sms_persists_and_redacts(store):
    payload = {"msg_id": "T1", "sender_phone": "+14045550199",
               "body": "Call me at (404) 555-9999"}
    msg = normalize_and_persist("sms", payload, store, store.org_id)
    assert msg.channel == "sms"
    assert "<PHONE>" in msg.body_redacted
    assert msg.body == "Call me at (404) 555-9999"     # raw kept
    assert msg.dedupe_hash
    assert msg.id in store.messages


def test_normalize_dedupe(store):
    payload = {"msg_id": "T2", "sender_phone": "+14045550199",
               "body": "duplicate"}
    normalize_and_persist("sms", payload, store, store.org_id)
    with pytest.raises(DuplicateMessage):
        normalize_and_persist("sms", payload, store, store.org_id)


def test_normalize_unknown_channel(store):
    with pytest.raises(ValueError):
        normalize_and_persist("carrier_pigeon", {}, store, store.org_id)


def test_normalize_audit_event_written(store):
    payload = {"msg_id": "T3", "sender_phone": "+14045550199", "body": "hello"}
    msg = normalize_and_persist("sms", payload, store, store.org_id)
    events = [e for e in store.audit if e.get("entity_id") == msg.id]
    assert events, "expected at least one audit_event for the new message"
    assert events[0]["action"] == "L2.normalized"
