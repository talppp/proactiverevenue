"""Contract tests for the SmsInboxStore interface.

Run against InMemorySmsInboxStore always. If `TEST_POSTGRES_URL` is set
(e.g. CI with a real Postgres), runs the same suite against
PostgresSmsInboxStore. The migration in migrations/001_sms_inbox_raw.sql
must already be applied on that DB.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from app.sms_inbox_store import (
    InMemorySmsInboxStore,
    PostgresSmsInboxStore,
    compute_msg_id,
)


def _new_inmem():
    return InMemorySmsInboxStore()


def _new_pg():
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL not set")
    s = PostgresSmsInboxStore(url)
    # Wipe the staging table between test runs.
    from sqlalchemy import text

    with s.engine.begin() as conn:
        conn.execute(text("TRUNCATE sms_inbox_raw CASCADE"))
        conn.execute(text("TRUNCATE llm_spend"))
    return s


@pytest.fixture(params=[_new_inmem, _new_pg], ids=["inmemory", "postgres"])
def any_store(request):
    return request.param()


_RECEIVED = datetime(2026, 5, 21, 11, 0, 0, tzinfo=timezone.utc)


def test_insert_then_duplicate(any_store):
    row1, new1 = any_store.insert_new("+1404", None, "hi", _RECEIVED)
    row2, new2 = any_store.insert_new("+1404", None, "hi", _RECEIVED)
    assert new1 is True and new2 is False
    assert row1.msg_id == row2.msg_id == compute_msg_id("+1404", "hi", _RECEIVED)


def test_mark_handed_off(any_store):
    row, _ = any_store.insert_new("+1404", None, "hi", _RECEIVED)
    any_store.mark_handed_off(row.msg_id)
    r = any_store.fetch(row.msg_id)
    assert r.status == "handed_off"
    assert r.handed_off_at is not None


def test_attempt_failed_then_dead_letter(any_store):
    row, _ = any_store.insert_new("+1404", None, "hi", _RECEIVED)
    s1 = any_store.mark_attempt_failed(row.msg_id, "boom", max_attempts=3)
    assert s1 == "new"
    any_store.mark_attempt_failed(row.msg_id, "boom", max_attempts=3)
    s3 = any_store.mark_attempt_failed(row.msg_id, "boom", max_attempts=3)
    assert s3 == "dead_letter"
    r = any_store.fetch(row.msg_id)
    assert r.attempts == 3 and r.last_error == "boom"


def test_reset_for_replay_only_dead_letter(any_store):
    row, _ = any_store.insert_new("+1404", None, "hi", _RECEIVED)
    # Not dead_letter → reset is a no-op
    assert any_store.reset_for_replay(row.msg_id) is False
    # Make it dead_letter, then reset
    for _ in range(5):
        any_store.mark_attempt_failed(row.msg_id, "x", max_attempts=3)
    assert any_store.fetch(row.msg_id).status == "dead_letter"
    assert any_store.reset_for_replay(row.msg_id) is True
    r = any_store.fetch(row.msg_id)
    assert r.status == "new" and r.attempts == 0 and r.last_error is None


def test_recent_by_sender_orders_and_limits(any_store):
    for i in range(4):
        any_store.insert_new(
            "+1404", None, f"msg {i}",
            datetime(2026, 5, 30, 10, i, 0, tzinfo=timezone.utc),
        )
    any_store.insert_new(
        "+1999", None, "other sender",
        datetime(2026, 5, 30, 10, 5, 0, tzinfo=timezone.utc),
    )
    recent = any_store.recent_by_sender("+1404", limit=3)
    assert len(recent) == 3
    assert all(r.from_number == "+1404" for r in recent)
    # most-recent-first
    assert recent[0].received_at >= recent[1].received_at >= recent[2].received_at


def test_feedback_roundtrip(any_store):
    row, _ = any_store.insert_new("+1404", None, "needs correction", _RECEIVED)
    assert any_store.insert_feedback(row.msg_id, "billing", "P1", "wrong lane") is True
    assert any_store.insert_feedback("nonexistent", "billing", None, None) is False
    items = any_store.list_feedback(limit=10)
    assert len(items) == 1
    assert items[0]["msg_id"] == row.msg_id
    assert items[0]["corrected_topic"] == "billing"
    assert items[0]["corrected_urgency"] == "P1"


def test_llm_spend_accumulates(any_store):
    assert any_store.llm_spend_today() == pytest.approx(0.0)
    assert any_store.add_llm_spend(0.10) == pytest.approx(0.10)
    assert any_store.add_llm_spend(0.05) == pytest.approx(0.15)
    assert any_store.llm_spend_today() == pytest.approx(0.15)


def test_counts_by_status(any_store):
    a, _ = any_store.insert_new("+1404", None, "a", _RECEIVED)
    b, _ = any_store.insert_new(
        "+1404",
        None,
        "b",
        datetime(2026, 5, 21, 11, 1, 0, tzinfo=timezone.utc),
    )
    any_store.mark_handed_off(a.msg_id)
    for _ in range(3):
        any_store.mark_attempt_failed(b.msg_id, "x", max_attempts=3)
    counts = any_store.counts_by_status()
    assert counts["handed_off"] == 1
    assert counts["dead_letter"] == 1
    assert counts["new"] == 0
