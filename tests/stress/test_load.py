"""Stress / load tests for the L1 SMS-phone path.

Run separately from the unit suite:
    pytest tests/stress -v --tb=short

These exercise concurrency, burst handling, and idempotency under
contention. They use the in-memory store; the same shape applies to the
Postgres impl, but those races would only show up against a real DB.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient


_PAYLOAD = {
    "from_number": "+14045551234",
    "to_number": "+14045559876",
    "body": "stress test message",
    "received_at_phone": "2026-05-21T11:59:50+00:00",
}
_AUTH = {"Authorization": "Bearer primary-secret"}


def test_webhook_burst_500_unique_messages(client, store, pipeline):
    """500 distinct messages POSTed serially; all must be queued and
    handed off. Asserts throughput, no duplicates, no drops."""
    t0 = time.perf_counter()
    posted = 0
    for i in range(500):
        payload = {**_PAYLOAD, "from_number": f"+1404555{i:04d}"}
        r = client.post("/webhooks/sms_phone", json=payload, headers=_AUTH)
        assert r.status_code == 200, r.text
        assert r.json()["duplicate"] is False
        posted += 1
    elapsed = time.perf_counter() - t0
    rate = posted / elapsed
    print(f"\nwebhook burst: {posted} msgs in {elapsed:.2f}s ({rate:.0f} msg/s)")
    counts = store.counts_by_status()
    assert counts["handed_off"] == 500
    assert counts["new"] == 0
    assert counts["dead_letter"] == 0
    assert len(pipeline.received) == 500
    # Per-message round-trip (incl. BG task) should average well under 10ms.
    assert elapsed < 10.0, f"500 msgs took {elapsed:.2f}s — TestClient regressed?"


def test_webhook_idempotency_under_concurrent_duplicates(client, store, pipeline):
    """50 threads each POST the same payload 5 times → 250 requests for
    one msg_id. Pipeline must see it exactly once; store has exactly
    one row."""
    seen = {"new": 0, "duplicate": 0, "other": 0}
    lock = __import__("threading").Lock()

    def hammer(_):
        out: list[str] = []
        for _i in range(5):
            r = client.post("/webhooks/sms_phone", json=_PAYLOAD, headers=_AUTH)
            assert r.status_code == 200, r.text
            out.append("duplicate" if r.json()["duplicate"] else "new")
        return out

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
        results = list(pool.map(hammer, range(50)))
    for batch in results:
        for k in batch:
            with lock:
                seen[k] = seen.get(k, 0) + 1

    print(f"\nconcurrent dup hammer results: {seen}")
    assert seen["new"] == 1, f"expected exactly 1 new insert, got {seen['new']}"
    assert seen["duplicate"] == 249
    assert sum(store.counts_by_status().values()) == 1
    assert len(pipeline.received) == 1


def test_webhook_unique_id_per_message_under_load(client, store):
    """Vary one field at a time and assert msg_id is a 1:1 function of
    (from_number, body, received_at_phone)."""
    seen_ids: set[str] = set()
    for i in range(100):
        for variant in ("from_number", "body", "received_at_phone"):
            payload = dict(_PAYLOAD)
            if variant == "from_number":
                payload["from_number"] = f"+1404555{i:04d}"
            elif variant == "body":
                payload["body"] = f"body variant {i}"
            else:
                # Spread across minute+second so i=0..99 yields 100 distinct
                # timestamps (i % 60 would wrap and re-collide with i=0).
                payload["received_at_phone"] = (
                    f"2026-05-21T11:{i // 60:02d}:{i % 60:02d}+00:00"
                )
            r = client.post("/webhooks/sms_phone", json=payload, headers=_AUTH)
            mid = r.json()["msg_id"]
            assert mid not in seen_ids, f"collision: {mid}"
            seen_ids.add(mid)
    assert len(seen_ids) == 300
    assert store.counts_by_status()["handed_off"] == 300


def test_recovery_loop_drains_large_backlog(store, pipeline, now_func):
    """1000 stuck rows; the loop drains them in batches."""
    from app.jobs import sms_poll
    from app import config

    for i in range(1000):
        store.insert_new(
            from_number=f"+1404555{i:04d}",
            to_number=None,
            body=f"backlog {i}",
            received_at_phone=datetime(
                2026, 5, 21, 11, 0, 0, tzinfo=timezone.utc
            ),
        )
    now_func.advance(120)  # age past threshold

    async def drain_to_empty():
        total = 0
        max_iters = 100  # safety bound
        for _ in range(max_iters):
            handed = await sms_poll.drain_stuck(store, pipeline)
            total += handed
            if handed == 0:
                break
        return total

    handed = asyncio.run(drain_to_empty())
    assert handed == 1000
    counts = store.counts_by_status()
    assert counts["handed_off"] == 1000
    assert counts["new"] == 0
    # With default batch size 50, 1000 messages need 20 ticks.
    print(
        f"\nrecovery loop drained 1000 stuck rows; batch_size={config.SMS_POLL_BATCH_SIZE}"
    )


def test_recovery_loop_isolates_failures(store, pipeline, now_func):
    """Out of 200 stuck rows, 50 fail in the pipeline. The 150 good ones
    still get handed off; the 50 bad rows accumulate retries."""
    from app.jobs import sms_poll
    from app.sms_inbox_store import compute_msg_id

    bad_ids: set[str] = set()
    for i in range(200):
        body = f"row {i}"
        rcv = datetime(2026, 5, 21, 11, 0, 0, tzinfo=timezone.utc)
        store.insert_new(
            from_number=f"+1404555{i:04d}",
            to_number=None,
            body=body,
            received_at_phone=rcv,
        )
        if i % 4 == 0:
            bad_ids.add(compute_msg_id(f"+1404555{i:04d}", body, rcv))
    pipeline.fail_for_msg_ids.update(bad_ids)
    now_func.advance(120)

    async def drain():
        for _ in range(10):
            await sms_poll.drain_stuck(store, pipeline)

    asyncio.run(drain())
    counts = store.counts_by_status()
    assert counts["handed_off"] == 150
    assert counts["new"] + counts["dead_letter"] == 50


def test_admin_inject_and_replay_roundtrip(client, store, pipeline):
    """50 injected messages, half forced to dead_letter, all replayed.
    End state: all 50 handed off, pipeline saw each exactly twice
    (initial inject attempt + post-replay attempt for the failed ones)."""
    from app import config
    from app.sms_inbox_store import compute_msg_id

    admin = {"Authorization": "Bearer admin-secret"}
    msg_ids: list[str] = []
    for i in range(50):
        payload = {
            **_PAYLOAD,
            "from_number": f"+1404555{i:04d}",
            "received_at_phone": f"2026-05-21T11:00:{i:02d}+00:00",
        }
        mid = compute_msg_id(
            payload["from_number"],
            payload["body"],
            datetime.fromisoformat(payload["received_at_phone"]),
        )
        msg_ids.append(mid)
        # Half are pre-flagged to fail.
        if i % 2 == 0:
            pipeline.fail_for_msg_ids.add(mid)
        r = client.post("/admin/sms_inbox_raw/inject", json=payload, headers=admin)
        assert r.status_code == 200

    # Force the failing ones to dead_letter (BG task already burned one
    # attempt — finish the rest).
    for mid in msg_ids[::2]:
        for _ in range(config.SMS_POLL_MAX_ATTEMPTS - 1):
            store.mark_attempt_failed(mid, "stress", config.SMS_POLL_MAX_ATTEMPTS)

    counts = store.counts_by_status()
    assert counts["dead_letter"] == 25
    assert counts["handed_off"] == 25

    # Lift the failures and replay.
    pipeline.fail_for_msg_ids.clear()
    for mid in msg_ids[::2]:
        r = client.post(f"/admin/sms_inbox_raw/{mid}/replay", headers=admin)
        assert r.status_code == 200

    counts2 = store.counts_by_status()
    assert counts2["handed_off"] == 50
    assert counts2["dead_letter"] == 0
    assert counts2["new"] == 0
