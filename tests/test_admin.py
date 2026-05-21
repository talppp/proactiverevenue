from __future__ import annotations

from datetime import datetime, timezone

from app import config
from app.sms_inbox_store import compute_msg_id

PAYLOAD = {
    "from_number": "+14045551234",
    "to_number": None,
    "body": "showing request",
    "received_at_phone": "2026-05-21T11:30:00+00:00",
}


def _admin() -> dict[str, str]:
    return {"Authorization": "Bearer admin-secret"}


def _sms() -> dict[str, str]:
    return {"Authorization": "Bearer primary-secret"}


def test_admin_rejects_wrong_bearer(client):
    r = client.post(
        "/admin/sms_inbox_raw/inject",
        json=PAYLOAD,
        headers={"Authorization": "Bearer not-admin"},
    )
    assert r.status_code == 401


def test_inject_stages_and_hands_off(client, store, pipeline):
    r = client.post("/admin/sms_inbox_raw/inject", json=PAYLOAD, headers=_admin())
    assert r.status_code == 200
    msg_id = r.json()["msg_id"]
    assert r.json()["duplicate"] is False
    row = store.fetch(msg_id)
    assert row is not None and row.status == "handed_off"
    assert len(pipeline.received) == 1


def test_inject_is_idempotent_with_webhook(client, store, pipeline):
    r1 = client.post("/webhooks/sms_phone", json=PAYLOAD, headers=_sms())
    assert r1.status_code == 200
    r2 = client.post("/admin/sms_inbox_raw/inject", json=PAYLOAD, headers=_admin())
    assert r2.status_code == 200
    assert r2.json()["duplicate"] is True
    assert len(pipeline.received) == 1


def test_replay_dead_letter_row(client, store, pipeline):
    received_at_phone = datetime.fromisoformat(PAYLOAD["received_at_phone"])
    msg_id = compute_msg_id(PAYLOAD["from_number"], PAYLOAD["body"], received_at_phone)

    pipeline.fail_for_msg_ids.add(msg_id)
    client.post("/webhooks/sms_phone", json=PAYLOAD, headers=_sms())
    # The webhook's BG task put one attempt on the row; bump the rest.
    for _ in range(config.SMS_POLL_MAX_ATTEMPTS - 1):
        store.mark_attempt_failed(msg_id, "boom", config.SMS_POLL_MAX_ATTEMPTS)
    assert store.fetch(msg_id).status == "dead_letter"

    pipeline.fail_for_msg_ids.discard(msg_id)
    r = client.post(f"/admin/sms_inbox_raw/{msg_id}/replay", headers=_admin())
    assert r.status_code == 200
    row = store.fetch(msg_id)
    assert row.status == "handed_off"
    assert row.attempts == 0


def test_replay_404_when_not_dead_lettered(client):
    r = client.post(
        "/admin/sms_inbox_raw/does-not-exist/replay", headers=_admin()
    )
    assert r.status_code == 404


def test_health_reports_counts(client, store):
    store.insert_new(
        from_number="+14045551111",
        to_number=None,
        body="a",
        received_at_phone=datetime(2026, 5, 21, 11, 0, 0, tzinfo=timezone.utc),
    )
    store.insert_new(
        from_number="+14045552222",
        to_number=None,
        body="b",
        received_at_phone=datetime(2026, 5, 21, 11, 1, 0, tzinfo=timezone.utc),
    )
    r = client.get("/admin/sms_inbox_raw/health", headers=_admin())
    assert r.status_code == 200
    counts = r.json()
    assert counts["new"] == 2
    assert counts["handed_off"] == 0
    assert counts["dead_letter"] == 0
