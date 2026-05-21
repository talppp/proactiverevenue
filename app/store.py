"""
Storage layer with two interchangeable backends:

  PostgresStore   - real Postgres via SQLAlchemy. Used in CI and on Render.
  InMemoryStore   - dict-backed mock. Used for the demo + unit tests; lets
                    the demo run on any machine with no Postgres install.

The Store interface is intentionally narrow - only the operations the
pipeline performs. Adding more queries (for dashboards etc.) means adding
named methods, never raw SQL outside this file.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.config import DB_URL, ORG_NAME, ORG_TIMEZONE, SCHEMA_SQL_PATH, TEAM_SEED


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ----------------------------------------------------------------------
# Domain records (kept as plain dataclasses, channel-agnostic).
# ----------------------------------------------------------------------
@dataclass
class InboundMessage:
    id: str
    org_id: str
    channel: str
    provider_msg_id: str | None
    sender_handle: str
    subject: str | None
    body: str
    body_redacted: str
    language: str
    received_at: datetime
    dedupe_hash: str
    raw_payload: dict = field(default_factory=dict)


@dataclass
class Classification:
    id: str
    message_id: str
    model_name: str
    urgency: str
    urgency_confidence: float
    topic: str
    topic_confidence: float
    secondary_topic: str | None
    is_business: bool
    is_follow_up: bool
    county_detected: str | None
    listing_detected: str | None
    summary: str
    suggested_reply: str
    reasoning: str
    cost_usd: float = 0.0
    latency_ms: int = 0


@dataclass
class RoutingDecision:
    id: str
    message_id: str
    classification_id: str
    primary_owner: str | None   # display_name, simplified for demo
    fallback_owner: str | None
    rule_id_matched: str
    reason: str
    is_archive: bool
    is_unsure: bool


@dataclass
class DeliveryEvent:
    id: str
    routing_id: str
    delivery_kind: str
    target_handle: str
    status: str            # 'sent' | 'queued' | 'skipped' | 'failed'
    idempotency_key: str
    sent_at: datetime
    error: str | None = None


# ----------------------------------------------------------------------
# Abstract store + two implementations
# ----------------------------------------------------------------------
class Store:
    # Schema lifecycle
    def init_schema(self) -> None: raise NotImplementedError
    def seed(self) -> None: raise NotImplementedError

    # Writes
    def insert_message(self, m: InboundMessage) -> None: raise NotImplementedError
    def insert_classification(self, c: Classification) -> None: raise NotImplementedError
    def insert_routing(self, r: RoutingDecision) -> None: raise NotImplementedError
    def insert_delivery(self, d: DeliveryEvent) -> None: raise NotImplementedError
    def insert_audit(self, **kw) -> None: raise NotImplementedError

    # Reads
    def existing_dedupe(self, dedupe_hash: str) -> bool: raise NotImplementedError
    def existing_idempotency(self, key: str) -> bool: raise NotImplementedError
    def team_member(self, name: str) -> dict | None: raise NotImplementedError


# ----------------------------------------------------------------------
class InMemoryStore(Store):
    def __init__(self) -> None:
        self.messages: dict[str, InboundMessage] = {}
        self.classifications: dict[str, Classification] = {}
        self.routings: dict[str, RoutingDecision] = {}
        self.deliveries: dict[str, DeliveryEvent] = {}
        self.audit: list[dict] = []
        self.team: dict[str, dict] = {}
        self.org_id: str = ""

    def init_schema(self) -> None:
        pass

    def seed(self) -> None:
        self.org_id = str(uuid.uuid4())
        for display_name, role, counties, ah, mx in TEAM_SEED:
            self.team[display_name] = {
                "id": str(uuid.uuid4()),
                "org_id": self.org_id,
                "display_name": display_name,
                "role": role,
                "counties": counties,
                "after_hours_ok": ah,
                "max_open_threads": mx,
            }

    def insert_message(self, m): self.messages[m.id] = m
    def insert_classification(self, c): self.classifications[c.id] = c
    def insert_routing(self, r): self.routings[r.id] = r
    def insert_delivery(self, d): self.deliveries[d.id] = d
    def insert_audit(self, **kw):
        kw.setdefault("created_at", _utcnow().isoformat())
        self.audit.append(kw)

    def existing_dedupe(self, h):
        return any(m.dedupe_hash == h for m in self.messages.values())
    def existing_idempotency(self, key):
        return any(d.idempotency_key == key for d in self.deliveries.values())
    def team_member(self, name):
        return self.team.get(name)


# ----------------------------------------------------------------------
class PostgresStore(Store):
    def __init__(self, db_url: str) -> None:
        self.engine: Engine = create_engine(db_url, future=True)
        self.org_id: str = ""

    def init_schema(self) -> None:
        sql = SCHEMA_SQL_PATH.read_text(encoding="utf-8")
        # Postgres needs DDL split into individual statements only if there
        # are stored procedures / DO blocks. Ours is plain CREATE - one shot.
        with self.engine.begin() as conn:
            conn.exec_driver_sql(sql)

    def seed(self) -> None:
        with self.engine.begin() as conn:
            row = conn.execute(text("SELECT id FROM org WHERE name=:n"),
                               {"n": ORG_NAME}).fetchone()
            if row:
                self.org_id = str(row[0])
            else:
                self.org_id = str(uuid.uuid4())
                conn.execute(text("""INSERT INTO org (id, name, timezone)
                                     VALUES (:id, :n, :tz)"""),
                             {"id": self.org_id, "n": ORG_NAME, "tz": ORG_TIMEZONE})
            for display_name, role, counties, ah, mx in TEAM_SEED:
                conn.execute(text("""
                    INSERT INTO team_member (org_id, display_name, role, counties,
                                             after_hours_ok, max_open_threads)
                    VALUES (:org, :n, :r, :c, :ah, :mx)
                    ON CONFLICT DO NOTHING"""),
                    {"org": self.org_id, "n": display_name, "r": role,
                     "c": counties, "ah": ah, "mx": mx})

    def insert_message(self, m):
        with self.engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO inbound_message
                  (id, org_id, channel_account_id, channel, provider_msg_id,
                   sender_handle, subject, body, body_redacted, language,
                   received_at, dedupe_hash, raw_payload)
                VALUES
                  (:id, :org, :cha_acct, :ch, :pmid, :sh, :sub, :b, :br, :lang,
                   :rcv, :h, :raw::jsonb)
                ON CONFLICT DO NOTHING"""),
                {"id": m.id, "org": self.org_id,
                 "cha_acct": self._channel_account(m.channel),
                 "ch": m.channel, "pmid": m.provider_msg_id,
                 "sh": m.sender_handle, "sub": m.subject, "b": m.body,
                 "br": m.body_redacted, "lang": m.language,
                 "rcv": m.received_at, "h": m.dedupe_hash,
                 "raw": json.dumps(m.raw_payload)})

    def _channel_account(self, channel: str) -> str:
        # Create-on-demand minimal channel_account row.
        with self.engine.begin() as conn:
            row = conn.execute(text("""SELECT id FROM channel_account
                                        WHERE org_id=:o AND channel=:c
                                        LIMIT 1"""),
                               {"o": self.org_id, "c": channel}).fetchone()
            if row:
                return str(row[0])
            cid = str(uuid.uuid4())
            conn.execute(text("""
                INSERT INTO channel_account (id, org_id, channel, account_label, status)
                VALUES (:id, :o, :c, :lbl, 'active')"""),
                {"id": cid, "o": self.org_id, "c": channel,
                 "lbl": f"demo_{channel}"})
            return cid

    def insert_classification(self, c):
        with self.engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO classification
                  (id, message_id, model_name, model_version, urgency,
                   urgency_confidence, topic, topic_confidence, secondary_topic,
                   is_business, is_follow_up, summary, suggested_reply,
                   reasoning, cost_usd, latency_ms)
                VALUES (:id, :m, :mn, '1.0', :u, :uc, :t, :tc, :st, :ib, :ifu,
                        :sum, :rep, :rsn, :cost, :lat)"""),
                {"id": c.id, "m": c.message_id, "mn": c.model_name,
                 "u": c.urgency, "uc": c.urgency_confidence,
                 "t": c.topic, "tc": c.topic_confidence,
                 "st": c.secondary_topic, "ib": c.is_business,
                 "ifu": c.is_follow_up, "sum": c.summary,
                 "rep": c.suggested_reply, "rsn": c.reasoning,
                 "cost": c.cost_usd, "lat": c.latency_ms})

    def insert_routing(self, r):
        with self.engine.begin() as conn:
            primary = self._team_id(r.primary_owner) if r.primary_owner else None
            fallback = self._team_id(r.fallback_owner) if r.fallback_owner else None
            conn.execute(text("""
                INSERT INTO routing_decision
                  (id, message_id, classification_id, primary_owner_id,
                   fallback_owner_id, rule_id_matched, reason, is_archive,
                   is_unsure)
                VALUES (:id, :m, :c, :p, :f, :rid, :rsn, :ar, :un)"""),
                {"id": r.id, "m": r.message_id, "c": r.classification_id,
                 "p": primary, "f": fallback, "rid": r.rule_id_matched,
                 "rsn": r.reason, "ar": r.is_archive, "un": r.is_unsure})

    def _team_id(self, name: str) -> str | None:
        with self.engine.begin() as conn:
            row = conn.execute(text("""SELECT id FROM team_member
                                        WHERE org_id=:o AND display_name=:n"""),
                               {"o": self.org_id, "n": name}).fetchone()
            return str(row[0]) if row else None

    def insert_delivery(self, d):
        with self.engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO delivery_log
                  (id, routing_id, delivery_kind, target_handle, delivered_at,
                   error, retry_count, idempotency_key)
                VALUES (:id, :r, :k, :h, :dt, :e, 0, :idk)
                ON CONFLICT (idempotency_key) DO NOTHING"""),
                {"id": d.id, "r": d.routing_id, "k": d.delivery_kind,
                 "h": d.target_handle, "dt": d.sent_at,
                 "e": d.error, "idk": d.idempotency_key})

    def insert_audit(self, **kw):
        with self.engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO audit_event (org_id, actor, action, entity_kind,
                                         entity_id, payload)
                VALUES (:o, :a, :ac, :ek, :ei, :p::jsonb)"""),
                {"o": self.org_id, "a": kw.get("actor", "system"),
                 "ac": kw["action"], "ek": kw.get("entity_kind"),
                 "ei": kw.get("entity_id"),
                 "p": json.dumps(kw.get("payload", {}))})

    def existing_dedupe(self, h):
        with self.engine.begin() as conn:
            row = conn.execute(text("SELECT 1 FROM inbound_message WHERE dedupe_hash=:h LIMIT 1"),
                               {"h": h}).fetchone()
            return bool(row)

    def existing_idempotency(self, key):
        with self.engine.begin() as conn:
            row = conn.execute(text("SELECT 1 FROM delivery_log WHERE idempotency_key=:k LIMIT 1"),
                               {"k": key}).fetchone()
            return bool(row)

    def team_member(self, name):
        with self.engine.begin() as conn:
            row = conn.execute(text("""SELECT display_name, role, counties,
                                              after_hours_ok, max_open_threads
                                         FROM team_member
                                         WHERE org_id=:o AND display_name=:n"""),
                               {"o": self.org_id, "n": name}).fetchone()
            if not row:
                return None
            return {"display_name": row[0], "role": row[1], "counties": row[2],
                    "after_hours_ok": row[3], "max_open_threads": row[4]}


# ----------------------------------------------------------------------
def make_store() -> Store:
    """Pick the backend based on env."""
    if DB_URL == "memory":
        s = InMemoryStore()
    else:
        s = PostgresStore(DB_URL)
    return s


def sha1_dedupe(channel: str, sender: str, body: str) -> str:
    h = hashlib.sha1()
    h.update(f"{channel}|{sender}|{body[:200]}".encode())
    return h.hexdigest()
