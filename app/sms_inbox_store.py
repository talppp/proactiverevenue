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

    def recent_by_sender(self, from_number: str, limit: int) -> list[SmsRow]:
        """Most-recent-first message history for one sender — the
        contact-memory lookup the classifier uses for thread context."""
        ...

    def insert_feedback(
        self,
        msg_id: str,
        corrected_topic: str | None,
        corrected_urgency: str | None,
        note: str | None,
    ) -> bool:
        """Record an operator correction against a staged message.
        Returns False when msg_id doesn't exist (no orphan feedback)."""
        ...

    def list_feedback(self, limit: int) -> list[dict]:
        """Most-recent-first corrections, for eval-set export."""
        ...

    def add_llm_spend(self, usd: float) -> float:
        """Accumulate LLM spend for today (UTC). Returns the new day total."""
        ...

    def llm_spend_today(self) -> float: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class InMemorySmsInboxStore:
    """Threadsafe in-memory store for the demo and tests."""

    _rows: dict[str, SmsRow] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _now: Callable[[], datetime] = field(default=_utcnow)
    _feedback: list[dict] = field(default_factory=list)
    _spend: dict[str, float] = field(default_factory=dict)  # 'YYYY-MM-DD' -> usd

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

    def recent_by_sender(self, from_number: str, limit: int) -> list[SmsRow]:
        with self._lock:
            hits = [r for r in self._rows.values() if r.from_number == from_number]
            hits.sort(key=lambda r: r.received_at, reverse=True)
            return hits[:limit]

    def insert_feedback(
        self,
        msg_id: str,
        corrected_topic: str | None,
        corrected_urgency: str | None,
        note: str | None,
    ) -> bool:
        with self._lock:
            if msg_id not in self._rows:
                return False
            self._feedback.append(
                {
                    "msg_id": msg_id,
                    "corrected_topic": corrected_topic,
                    "corrected_urgency": corrected_urgency,
                    "note": note,
                    "created_at": self._now(),
                }
            )
            return True

    def list_feedback(self, limit: int) -> list[dict]:
        with self._lock:
            return list(reversed(self._feedback))[:limit]

    def add_llm_spend(self, usd: float) -> float:
        day = self._now().date().isoformat()
        with self._lock:
            self._spend[day] = self._spend.get(day, 0.0) + usd
            return self._spend[day]

    def llm_spend_today(self) -> float:
        day = self._now().date().isoformat()
        with self._lock:
            return self._spend.get(day, 0.0)


# ----------------------------------------------------------------------
class PostgresSmsInboxStore:
    """Postgres-backed implementation. Matches the InMemory contract.

    The recovery loop runs single-instance (max_instances=1 + coalesce=True
    in the APScheduler config), so `fetch_stuck` does not need explicit
    row locking — the next tick won't fire until the current one finishes.
    If we ever scale to multiple poller instances, change the SELECT to
    use `FOR UPDATE SKIP LOCKED` inside a short transaction and stamp
    rows to an intermediate 'in_progress' status before handing off.
    """

    def __init__(self, db_url: str) -> None:
        # Local import keeps the dependency optional for the in-memory path.
        from sqlalchemy import create_engine

        # pool_pre_ping: validate a pooled connection before using it, so a
        #   dropped/stale connection (Supabase pooler recycling, network blip)
        #   transparently reconnects instead of raising.
        # pool_recycle: drop connections older than 5 min — the Supabase
        #   transaction pooler closes idle ones, and a recycled handle would
        #   otherwise surface as a confusing OperationalError.
        self.engine = create_engine(
            db_url,
            future=True,
            pool_pre_ping=True,
            pool_recycle=300,
        )

    def insert_new(
        self,
        from_number: str,
        to_number: str | None,
        body: str,
        received_at_phone: datetime,
    ) -> tuple[SmsRow, bool]:
        from sqlalchemy import text

        msg_id = compute_msg_id(from_number, body, received_at_phone)
        with self.engine.begin() as conn:
            inserted = conn.execute(
                text(
                    """
                    INSERT INTO sms_inbox_raw
                      (msg_id, from_number, to_number, body, received_at_phone)
                    VALUES (:msg_id, :from_n, :to_n, :body, :rcvd)
                    ON CONFLICT (msg_id) DO NOTHING
                    RETURNING msg_id
                    """
                ),
                {
                    "msg_id": msg_id,
                    "from_n": from_number,
                    "to_n": to_number,
                    "body": body,
                    "rcvd": received_at_phone,
                },
            ).fetchone()
            was_new = inserted is not None
            row = self._fetch(conn, msg_id)
        assert row is not None  # we just inserted or hit an existing row
        return row, was_new

    def mark_handed_off(self, msg_id: str) -> None:
        from sqlalchemy import text

        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE sms_inbox_raw
                       SET status='handed_off',
                           handed_off_at=now(),
                           last_error=NULL
                     WHERE msg_id=:id
                    """
                ),
                {"id": msg_id},
            )

    def mark_attempt_failed(
        self, msg_id: str, error: str, max_attempts: int
    ) -> Status:
        from sqlalchemy import text

        with self.engine.begin() as conn:
            new_status = conn.execute(
                text(
                    """
                    UPDATE sms_inbox_raw
                       SET attempts = attempts + 1,
                           last_error = :err,
                           status = CASE
                               WHEN attempts + 1 >= :maxn THEN 'dead_letter'
                               ELSE status
                           END
                     WHERE msg_id = :id
                    RETURNING status
                    """
                ),
                {"id": msg_id, "err": error[:500], "maxn": max_attempts},
            ).scalar_one_or_none()
        return new_status or "new"

    def fetch_stuck(self, threshold_seconds: int, batch_size: int) -> list[SmsRow]:
        from sqlalchemy import text

        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT msg_id, from_number, to_number, body,
                           received_at_phone, received_at, status, attempts,
                           last_error, handed_off_at
                      FROM sms_inbox_raw
                     WHERE status='new'
                       AND received_at < now() - make_interval(secs => :thr)
                  ORDER BY received_at
                     LIMIT :batch
                    """
                ),
                {"thr": threshold_seconds, "batch": batch_size},
            ).fetchall()
        return [self._row_from_db(r) for r in rows]

    def fetch(self, msg_id: str) -> SmsRow | None:
        with self.engine.begin() as conn:
            return self._fetch(conn, msg_id)

    def reset_for_replay(self, msg_id: str) -> bool:
        from sqlalchemy import text

        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    UPDATE sms_inbox_raw
                       SET status='new', attempts=0, last_error=NULL,
                           handed_off_at=NULL
                     WHERE msg_id=:id AND status='dead_letter'
                    RETURNING msg_id
                    """
                ),
                {"id": msg_id},
            ).fetchone()
        return row is not None

    def counts_by_status(self) -> dict[Status, int]:
        from sqlalchemy import text

        counts = {"new": 0, "handed_off": 0, "dead_letter": 0}
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT status, count(*) FROM sms_inbox_raw GROUP BY status"
                )
            ).fetchall()
        for status, n in rows:
            counts[status] = int(n)
        return counts

    def recent_by_sender(self, from_number: str, limit: int) -> list[SmsRow]:
        from sqlalchemy import text

        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT msg_id, from_number, to_number, body,
                           received_at_phone, received_at, status, attempts,
                           last_error, handed_off_at
                      FROM sms_inbox_raw
                     WHERE from_number = :f
                  ORDER BY received_at DESC
                     LIMIT :n
                    """
                ),
                {"f": from_number, "n": limit},
            ).fetchall()
        return [self._row_from_db(r) for r in rows]

    def insert_feedback(
        self,
        msg_id: str,
        corrected_topic: str | None,
        corrected_urgency: str | None,
        note: str | None,
    ) -> bool:
        from sqlalchemy import text

        with self.engine.begin() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM sms_inbox_raw WHERE msg_id=:id"),
                {"id": msg_id},
            ).fetchone()
            if not exists:
                return False
            conn.execute(
                text(
                    """
                    INSERT INTO sms_feedback
                      (msg_id, corrected_topic, corrected_urgency, note)
                    VALUES (:id, :topic, :urg, :note)
                    """
                ),
                {"id": msg_id, "topic": corrected_topic,
                 "urg": corrected_urgency, "note": note},
            )
            return True

    def list_feedback(self, limit: int) -> list[dict]:
        from sqlalchemy import text

        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT msg_id, corrected_topic, corrected_urgency, note,
                           created_at
                      FROM sms_feedback
                  ORDER BY created_at DESC
                     LIMIT :n
                    """
                ),
                {"n": limit},
            ).fetchall()
        return [
            {
                "msg_id": r[0],
                "corrected_topic": r[1],
                "corrected_urgency": r[2],
                "note": r[3],
                "created_at": r[4],
            }
            for r in rows
        ]

    def add_llm_spend(self, usd: float) -> float:
        from sqlalchemy import text

        with self.engine.begin() as conn:
            total = conn.execute(
                text(
                    """
                    INSERT INTO llm_spend (day, usd)
                    VALUES (CURRENT_DATE, :usd)
                    ON CONFLICT (day) DO UPDATE SET usd = llm_spend.usd + :usd
                    RETURNING usd
                    """
                ),
                {"usd": usd},
            ).scalar_one()
        return float(total)

    def llm_spend_today(self) -> float:
        from sqlalchemy import text

        with self.engine.begin() as conn:
            total = conn.execute(
                text("SELECT usd FROM llm_spend WHERE day = CURRENT_DATE")
            ).scalar_one_or_none()
        return float(total) if total is not None else 0.0

    # ------------------------------------------------------------------
    def _fetch(self, conn, msg_id: str) -> SmsRow | None:
        from sqlalchemy import text

        row = conn.execute(
            text(
                """
                SELECT msg_id, from_number, to_number, body,
                       received_at_phone, received_at, status, attempts,
                       last_error, handed_off_at
                  FROM sms_inbox_raw
                 WHERE msg_id=:id
                """
            ),
            {"id": msg_id},
        ).fetchone()
        return self._row_from_db(row) if row else None

    @staticmethod
    def _row_from_db(r) -> SmsRow:
        return SmsRow(
            msg_id=r[0],
            from_number=r[1],
            to_number=r[2],
            body=r[3],
            received_at_phone=r[4],
            received_at=r[5],
            status=r[6],
            attempts=r[7],
            last_error=r[8],
            handed_off_at=r[9],
        )


def make_sms_inbox_store(db_url: str):
    """Factory matching the style of app.store.make_store()."""
    if not db_url or db_url == "memory":
        return InMemorySmsInboxStore()
    return PostgresSmsInboxStore(db_url)
