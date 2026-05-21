"""sms_inbox_raw store.

Staging table for SMS messages forwarded from Nathalie's iPhone via the
iOS Shortcuts automation. The webhook inserts here idempotently (keyed on
a deterministic msg_id) and the recovery loop drains anything the
in-process handoff couldn't complete.

Decoupled from the main `app/store.py` so it can be reasoned about, unit-
tested, and migrated to Postgres independently of the domain store. The
Postgres impl is sketched at the bottom (see comments); the InMemory impl
below is used by tests and by the demo path when DB_URL='memory'.

See docs/L1_SMS_Phone_Ingestion_Design.md for the full design.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable, Protocol

Status = str  # 'new' | 'handed_off' | 'dead_letter'


@dataclass
class SmsRow:
    msg_id: str
    from_number: str
    to_number: str | None
    body: str
    received_at_phone: datetime
    received_at: datetime
    status: Status = "new"
    attempts: int = 0
    last_error: str | None = None
    handed_off_at: datetime | None = None


def compute_msg_id(from_number: str, body: str, received_at_phone: datetime) -> str:
    """Deterministic id used as the staging-table primary key.

    Same (sender, body, phone-timestamp) tuple → same id, so an iPhone
    retry of the same message collides on the PK rather than producing a
    duplicate row. 0x1f separator prevents adjacent-field smuggling.
    """
    sep = b"\x1f"
    payload = (
        from_number.encode("utf-8")
        + sep
        + body.encode("utf-8")
        + sep
        + received_at_phone.isoformat().encode("utf-8")
    )
    return hashlib.sha256(payload).hexdigest()


class SmsInboxStore(Protocol):
    def insert_new(
        self,
        from_number: str,
        to_number: str | None,
        body: str,
        received_at_phone: datetime,
    ) -> tuple[SmsRow, bool]:
        """Returns (row, was_new). When was_new=False the row already
        existed and the caller should treat this as an idempotent no-op."""
        ...

    def mark_handed_off(self, msg_id: str) -> None: ...

    def mark_attempt_failed(
        self, msg_id: str, error: str, max_attempts: int
    ) -> Status:
        """Returns the new status after incrementing attempts."""
        ...

    def fetch_stuck(self, threshold_seconds: int, batch_size: int) -> list[SmsRow]: ...

    def fetch(self, msg_id: str) -> SmsRow | None: ...

    def reset_for_replay(self, msg_id: str) -> bool:
        """Set status='new' and attempts=0 on a dead_letter row. Returns
        True if a row was reset, False if no matching dead_letter row."""
        ...

    def counts_by_status(self) -> dict[Status, int]: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class InMemorySmsInboxStore:
    """Threadsafe in-memory store for the demo and tests."""

    _rows: dict[str, SmsRow] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _now: Callable[[], datetime] = field(default=_utcnow)

    def insert_new(
        self,
        from_number: str,
        to_number: str | None,
        body: str,
        received_at_phone: datetime,
    ) -> tuple[SmsRow, bool]:
        msg_id = compute_msg_id(from_number, body, received_at_phone)
        with self._lock:
            existing = self._rows.get(msg_id)
            if existing is not None:
                return existing, False
            row = SmsRow(
                msg_id=msg_id,
                from_number=from_number,
                to_number=to_number,
                body=body,
                received_at_phone=received_at_phone,
                received_at=self._now(),
            )
            self._rows[msg_id] = row
            return row, True

    def mark_handed_off(self, msg_id: str) -> None:
        with self._lock:
            row = self._rows.get(msg_id)
            if row is None:
                return
            row.status = "handed_off"
            row.handed_off_at = self._now()
            row.last_error = None

    def mark_attempt_failed(
        self, msg_id: str, error: str, max_attempts: int
    ) -> Status:
        with self._lock:
            row = self._rows.get(msg_id)
            if row is None:
                return "new"
            row.attempts += 1
            row.last_error = error[:500]
            if row.attempts >= max_attempts:
                row.status = "dead_letter"
            return row.status

    def fetch_stuck(self, threshold_seconds: int, batch_size: int) -> list[SmsRow]:
        cutoff = self._now().timestamp() - threshold_seconds
        with self._lock:
            stuck = [
                row
                for row in self._rows.values()
                if row.status == "new" and row.received_at.timestamp() <= cutoff
            ]
            stuck.sort(key=lambda r: r.received_at)
            return stuck[:batch_size]

    def fetch(self, msg_id: str) -> SmsRow | None:
        with self._lock:
            return self._rows.get(msg_id)

    def reset_for_replay(self, msg_id: str) -> bool:
        with self._lock:
            row = self._rows.get(msg_id)
            if row is None or row.status != "dead_letter":
                return False
            row.status = "new"
            row.attempts = 0
            row.last_error = None
            row.handed_off_at = None
            return True

    def counts_by_status(self) -> dict[Status, int]:
        counts: dict[str, int] = {"new": 0, "handed_off": 0, "dead_letter": 0}
        with self._lock:
            for row in self._rows.values():
                counts[row.status] = counts.get(row.status, 0) + 1
        return counts


# ----------------------------------------------------------------------
# Postgres impl sketch (real project, uses SQLAlchemy like app/store.py):
#
#     INSERT INTO sms_inbox_raw (msg_id, from_number, to_number, body,
#                                received_at_phone)
#     VALUES (:msg_id, :from_n, :to_n, :body, :rcvd_phone)
#     ON CONFLICT (msg_id) DO NOTHING
#     RETURNING msg_id
#     -- was_new = rowcount == 1
#
#     SELECT msg_id, from_number, to_number, body, received_at_phone,
#            received_at, status, attempts, last_error, handed_off_at
#       FROM sms_inbox_raw
#      WHERE status = 'new'
#        AND received_at < now() - make_interval(secs => :threshold)
#      ORDER BY received_at
#      LIMIT :batch
#      FOR UPDATE SKIP LOCKED;
#
#     UPDATE sms_inbox_raw
#        SET status = 'handed_off', handed_off_at = now(),
#            last_error = NULL
#      WHERE msg_id = :id;
#
#     UPDATE sms_inbox_raw
#        SET attempts = attempts + 1,
#            last_error = :err,
#            status = CASE WHEN attempts + 1 >= :max THEN 'dead_letter'
#                          ELSE status END
#      WHERE msg_id = :id
#      RETURNING status;
# ----------------------------------------------------------------------
