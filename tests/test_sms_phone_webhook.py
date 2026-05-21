from __future__ import annotations

from datetime import datetime

from app.sms_inbox_store import compute_msg_id

VALID_PAYLOAD = {
    "from_number": "+14045551234",
    "to_number": "+14045559876",
    "body": "Hi Nathalie, the buyer wants a Saturday showing.",
    "received_at_phone": "2026-05-21T11:59:50+00:00",
}


def _auth(token: str = "primary-secret") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_rejects_missing_bearer(client):
    r = client.post("/webhooks/sms_phone", json=VALID_PAYLOAD)
    assert r.status_code == 401


def test_rejects_bad_bearer(client):
    r = client.post(
        "/webhooks/sms_phone", json=VALID_PAYLOAD, headers=_auth("wrong-secret")
    )
    assert r.status_code == 401


def test_accepts_primary_bearer(client, pipeline):
    r = client.post("/webhooks/sms_phone", json=VALID_PAYLOAD, headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["duplicate"] is False
    assert len(body["msg_id"]) == 64  # sha256 hex
    assert len(pipeline.received) == 1
    channel, payload = pipeline.received[0]
    assert channel == "sms_phone"
    assert payload["from_number"] == "+14045551234"
    assert payload["body"] == VALID_PAYLOAD["body"]


def test_accepts_secondary_bearer(client, pipeline):
    r = client.post(
        "/webhooks/sms_phone",
        json=VALID_PAYLOAD,
        headers=_auth("secondary-secret"),
    )
    assert r.status_code == 200
    assert len(pipeline.received) == 1


def test_idempotent_duplicate_does_not_re_handoff(client, pipeline):
    r1 = client.post("/webhooks/sms_phone", json=VALID_PAYLOAD, headers=_auth())
    r2 = client.post("/webhooks/sms_phone", json=VALID_PAYLOAD, headers=_auth())
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["msg_id"] == r2.json()["msg_id"]
    assert r1.json()["duplicate"] is False
    assert r2.json()["duplicate"] is True
    assert len(pipeline.received) == 1  # pipeline only ever called once


def test_marks_handed_off_on_success(client, store):
    r = client.post("/webhooks/sms_phone", json=VALID_PAYLOAD, headers=_auth())
    msg_id = r.json()["msg_id"]
    row = store.fetch(msg_id)
    assert row is not None
    assert row.status == "handed_off"
    assert row.handed_off_at is not None
    assert row.attempts == 0


def test_pipeline_failure_leaves_row_for_recovery(client, store, pipeline):
    received_at_phone = datetime.fromisoformat(VALID_PAYLOAD["received_at_phone"])
    msg_id = compute_msg_id(
        VALID_PAYLOAD["from_number"], VALID_PAYLOAD["body"], received_at_phone
    )
    pipeline.fail_for_msg_ids.add(msg_id)

    r = client.post("/webhooks/sms_phone", json=VALID_PAYLOAD, headers=_auth())
    assert r.status_code == 200
    row = store.fetch(msg_id)
    assert row is not None
    assert row.status == "new"
    assert row.attempts == 1
    assert row.last_error is not None
    assert "stub failure" in row.last_error


def test_rejects_malformed_payload(client):
    r = client.post(
        "/webhooks/sms_phone",
        json={"from_number": "+1", "body": ""},  # missing received_at_phone, empty body
        headers=_auth(),
    )
    assert r.status_code == 422


def test_returns_503_when_no_tokens_configured(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "SMS_BEARER_TOKEN_PRIMARY", None)
    monkeypatch.setattr(config, "SMS_BEARER_TOKEN_SECONDARY", None)
    r = client.post("/webhooks/sms_phone", json=VALID_PAYLOAD, headers=_auth())
    assert r.status_code == 503
