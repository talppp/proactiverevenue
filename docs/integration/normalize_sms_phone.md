# Adding the `sms_phone` channel to `app/ingestion/normalize.py`

The L1 webhook (`app/ingestion/sms_phone.py`) hands the staged row to
the existing pipeline as:

```python
pipeline.handle_inbound(
    channel="sms_phone",
    payload={
        "msg_id":          "<sha256-of-from+body+received_at_phone>",
        "provider_msg_id": "<same as msg_id>",
        "from_number":     "+14045551234",
        "to_number":       "+14045559876",  # may be None
        "body":            "Hi Nathalie, ...",
        "received_at":     "2026-05-21T11:59:50+00:00",  # ISO 8601
    },
)
```

That payload reaches `normalize_and_persist(channel, payload, store, org_id)`
inside the existing `app/ingestion/normalize.py`. The channel needs to be
added to that file's dispatch table. The shape is identical to `'sms'`
except for the channel string, so the diff is small.

## Patch

Locate the dispatch table in `normalize.py` (looks something like):

```python
_NORMALIZERS = {
    "sms":          _normalize_sms,
    "whatsapp":     _normalize_whatsapp,
    "gmail":        _normalize_gmail,
    "apple_mail":   _normalize_apple_mail,
    "instagram":    _normalize_instagram,
    "test":         _normalize_test,
}
```

Add `sms_phone`. It can reuse `_normalize_sms` directly if its shape
matches, or use the small wrapper below for clarity (and so L4 rules
can read the channel string when deciding whether to treat
phone-mirrored SMS differently from a future direct-Twilio path):

```python
def _normalize_sms_phone(payload: dict, store: Store, org_id: str) -> InboundMessage:
    """L1 SMS from Nathalie's iPhone via the Apple Shortcuts forwarder.

    Payload shape (from app/ingestion/sms_phone.py):
        msg_id           sha256(from_number||body||received_at_phone), used as
                         both id and provider_msg_id so the standard dedupe
                         path in normalize_and_persist catches retries.
        from_number      E.164
        to_number        optional E.164 (Nathalie's number; for multi-line)
        body             the SMS body
        received_at      ISO 8601 from the phone's clock
    """
    sender_handle = payload["from_number"]
    body = payload["body"]
    received_at = datetime.fromisoformat(payload["received_at"])

    body_redacted = redact_pii(body)               # existing helper
    language     = detect_language(body_redacted)  # existing helper

    return InboundMessage(
        id=str(uuid.uuid4()),
        org_id=org_id,
        channel="sms_phone",
        provider_msg_id=payload["provider_msg_id"],
        sender_handle=sender_handle,
        subject=None,
        body=body,
        body_redacted=body_redacted,
        language=language,
        received_at=received_at,
        dedupe_hash=sha1_dedupe("sms_phone", sender_handle, body),
        raw_payload=payload,
    )


_NORMALIZERS["sms_phone"] = _normalize_sms_phone
```

## Routing-rules implication (L4)

`channel='sms_phone'` will flow through to `app/router/rules.py`'s
`decide(...)`. If any existing rules in `04_Rules_and_Routing/routing_rules.yaml`
look at `channel` and expect only `'sms'`, decide whether they should
also fire for `'sms_phone'`. In most cases the cleanest fix is a small
rule alias in the YAML:

```yaml
channel_aliases:
  sms_phone: sms
```

…or update each affected rule's `channel` matcher to `[sms, sms_phone]`.

## Test

In the real project's `09_Advanced_Tests/` directory, add a scenario
covering an L1 phone-SMS → full L2-L6 trace. Minimum assertion: the
audit_event table contains an `L3.classified` row with the right
`message_id` and the inbound_message row's `channel='sms_phone'`.
