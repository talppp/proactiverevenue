"""
Forward-compatible DB schema (Phase 1 -> Phase 5) for the AI Issue Router and
its downstream solutions. Emits:
  - 02_schema.sql           (Postgres DDL, drop-in)
  - 02_ER_diagram.png/.pdf  (CAD-style, consistent with the flow chart)
  - 02_DB_Schema_Forward_Compatible.docx
"""
import os
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyArrowPatch
from matplotlib.lines import Line2D
from _docx_helpers import new_doc, title, h1, h2, h3, body, bullet, table, code, picture

OUT = os.path.dirname(os.path.abspath(__file__))
SQL = os.path.join(OUT, "02_schema.sql")
PNG = os.path.join(OUT, "02_ER_diagram.png")
PDF = os.path.join(OUT, "02_ER_diagram.pdf")
DOCX= os.path.join(OUT, "02_DB_Schema_Forward_Compatible.docx")

# ===========================================================================
# SQL DDL
# ===========================================================================
SCHEMA_SQL = r"""-- =====================================================================
-- Apteker Realty  /  ProActive Revenue
-- AI Operating Platform - Forward-compatible schema (Phase 1 -> Phase 5)
-- Target: PostgreSQL 14+
-- Rev 1.0  /  2026-05-17
-- =====================================================================

-- Extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pg_trgm";    -- trigram indexes for fuzzy match

-- =====================================================================
-- ENUM types
-- =====================================================================
CREATE TYPE channel_kind   AS ENUM ('apple_mail','whatsapp','sms','manual');
CREATE TYPE urgency_tier   AS ENUM ('P0','P1','P2','P3','UNCLASSIFIED');
CREATE TYPE topic_code     AS ENUM (
  'buyer_lead','seller_lead','showing','tenant_issue','maintenance',
  'contract','document','vendor','admin','scheduling','billing',
  'deal','vip_client','personal','marketing_promo','spam','unknown'
);
CREATE TYPE team_role      AS ENUM ('owner','agent','back_office','admin');
CREATE TYPE delivery_kind  AS ENUM ('push','queue_card','digest','ai_draft','escalate_phone');
CREATE TYPE override_reason AS ENUM (
  'wrong_topic','wrong_urgency','wrong_owner','spam_missed','vip_missed','other');

-- =====================================================================
-- PHASE 1  -  Core: org, team, contacts, properties, channels, messages,
--                  classification, routing, delivery, overrides, audit
-- =====================================================================

CREATE TABLE org (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT NOT NULL,
  timezone    TEXT NOT NULL DEFAULT 'America/New_York',
  retention_days INT NOT NULL DEFAULT 180,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE team_member (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL REFERENCES org(id) ON DELETE CASCADE,
  display_name     TEXT NOT NULL,
  role             team_role NOT NULL,
  email            TEXT,
  phone            TEXT,
  after_hours_ok   BOOLEAN NOT NULL DEFAULT false,
  counties         TEXT[] NOT NULL DEFAULT '{}',     -- empty = any
  topics_primary   TEXT[] NOT NULL DEFAULT '{}',
  max_open_threads INT NOT NULL DEFAULT 999,         -- capacity ceiling
  active           BOOLEAN NOT NULL DEFAULT true,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX team_member_org_idx ON team_member (org_id);

CREATE TABLE team_availability (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  member_id   UUID NOT NULL REFERENCES team_member(id) ON DELETE CASCADE,
  starts_at   TIMESTAMPTZ NOT NULL,
  ends_at     TIMESTAMPTZ NOT NULL,
  kind        TEXT NOT NULL,    -- 'pto','sick','focus','available_override'
  comment     TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX team_availability_window_idx
  ON team_availability (member_id, starts_at, ends_at);

CREATE TABLE contact (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL REFERENCES org(id) ON DELETE CASCADE,
  display_name TEXT,
  is_vip       BOOLEAN NOT NULL DEFAULT false,
  lifecycle    TEXT,    -- 'lead','active_buyer','active_seller','tenant','vendor','past_client'
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  notes        JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX contact_org_idx ON contact (org_id);
CREATE INDEX contact_vip_idx ON contact (org_id) WHERE is_vip = true;
CREATE INDEX contact_name_trgm ON contact USING gin (display_name gin_trgm_ops);

CREATE TABLE contact_identity (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  contact_id  UUID NOT NULL REFERENCES contact(id) ON DELETE CASCADE,
  channel     channel_kind NOT NULL,
  identifier  TEXT NOT NULL,    -- phone for SMS/WA, email for mail, handle for IG
  verified    BOOLEAN NOT NULL DEFAULT false,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (channel, identifier)
);
CREATE INDEX contact_identity_contact_idx ON contact_identity (contact_id);

CREATE TABLE property (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL REFERENCES org(id) ON DELETE CASCADE,
  external_id   TEXT,                              -- MLS or internal id
  address       TEXT,
  city          TEXT,
  state         TEXT DEFAULT 'GA',
  county        TEXT,
  zip           TEXT,
  property_kind TEXT,        -- 'listing_for_sale','listing_for_rent','managed_rental','sold','off_market'
  metadata      JSONB NOT NULL DEFAULT '{}',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX property_org_idx ON property (org_id);
CREATE INDEX property_county_idx ON property (county);
CREATE INDEX property_external_id_idx ON property (external_id);
CREATE INDEX property_address_trgm ON property USING gin (address gin_trgm_ops);

CREATE TABLE contact_property (
  contact_id  UUID NOT NULL REFERENCES contact(id) ON DELETE CASCADE,
  property_id UUID NOT NULL REFERENCES property(id) ON DELETE CASCADE,
  relation    TEXT NOT NULL,    -- 'tenant','owner','prospect','past_buyer','past_seller'
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (contact_id, property_id, relation)
);

CREATE TABLE channel_account (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL REFERENCES org(id) ON DELETE CASCADE,
  channel         channel_kind NOT NULL,
  account_label   TEXT NOT NULL,        -- 'apteker_gmail','nathalie_icloud','wa_business','main_sms','ig_apteker'
  status          TEXT NOT NULL DEFAULT 'pending',   -- pending|active|error|revoked
  config_secret_ref TEXT,                -- pointer into secrets store, NOT the secret itself
  config_public   JSONB NOT NULL DEFAULT '{}',
  last_synced_at  TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX channel_account_unique
  ON channel_account (org_id, channel, account_label);

CREATE TABLE inbound_message (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL REFERENCES org(id) ON DELETE CASCADE,
  channel_account_id UUID NOT NULL REFERENCES channel_account(id),
  channel            channel_kind NOT NULL,
  provider_msg_id    TEXT,                                -- idempotency
  contact_id         UUID REFERENCES contact(id),
  sender_handle      TEXT NOT NULL,
  subject            TEXT,                                -- email only
  body               TEXT NOT NULL,
  body_redacted      TEXT,                                -- PII-stripped for LLM
  language           CHAR(2),
  thread_key         TEXT,                                -- provider thread id, when present
  received_at        TIMESTAMPTZ NOT NULL,
  ingested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  dedupe_hash        TEXT,                                -- sha1(channel+sender+body[:200])
  raw_payload        JSONB,                               -- redacted webhook body
  UNIQUE (channel, provider_msg_id)
);
CREATE INDEX inbound_message_org_received_idx
  ON inbound_message (org_id, received_at DESC);
CREATE INDEX inbound_message_contact_idx ON inbound_message (contact_id);
CREATE INDEX inbound_message_thread_idx  ON inbound_message (thread_key);
CREATE INDEX inbound_message_hash_idx    ON inbound_message (dedupe_hash);

CREATE TABLE message_attachment (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id   UUID NOT NULL REFERENCES inbound_message(id) ON DELETE CASCADE,
  storage_url  TEXT NOT NULL,
  mime_type    TEXT,
  size_bytes   BIGINT,
  transcript   TEXT,        -- voice notes -> text
  ocr_text     TEXT         -- image/pdf -> text
);
CREATE INDEX message_attachment_message_idx ON message_attachment (message_id);

CREATE TABLE classification (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id         UUID NOT NULL REFERENCES inbound_message(id) ON DELETE CASCADE,
  model_name         TEXT NOT NULL,
  model_version      TEXT NOT NULL,
  urgency            urgency_tier NOT NULL,
  urgency_confidence NUMERIC(4,3) NOT NULL,
  topic              topic_code NOT NULL,
  topic_confidence   NUMERIC(4,3) NOT NULL,
  secondary_topic    topic_code,             -- multi-label support
  is_business        BOOLEAN NOT NULL,
  is_follow_up       BOOLEAN NOT NULL DEFAULT false,
  sentiment          TEXT,
  county_detected    TEXT,
  property_id        UUID REFERENCES property(id),
  summary            TEXT,
  suggested_reply    TEXT,
  reasoning          TEXT,
  prompt_tokens      INT,
  completion_tokens  INT,
  cost_usd           NUMERIC(10,5),
  latency_ms         INT,
  classified_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX classification_message_idx ON classification (message_id);
CREATE INDEX classification_urgency_topic_idx ON classification (urgency, topic);

CREATE TABLE routing_rule_version (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL REFERENCES org(id) ON DELETE CASCADE,
  version     INT NOT NULL,
  yaml_source TEXT NOT NULL,
  yaml_hash   TEXT NOT NULL,
  created_by  TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, version)
);

CREATE TABLE routing_decision (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id        UUID NOT NULL REFERENCES inbound_message(id) ON DELETE CASCADE,
  classification_id UUID REFERENCES classification(id),
  rule_version_id   UUID REFERENCES routing_rule_version(id),
  primary_owner_id  UUID REFERENCES team_member(id),
  fallback_owner_id UUID REFERENCES team_member(id),
  rule_id_matched   TEXT,
  reason            TEXT,
  is_archive        BOOLEAN NOT NULL DEFAULT false,
  is_unsure         BOOLEAN NOT NULL DEFAULT false,
  decided_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX routing_decision_message_idx ON routing_decision (message_id);
CREATE INDEX routing_decision_owner_idx
  ON routing_decision (primary_owner_id, decided_at DESC);

CREATE TABLE delivery_log (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  routing_id      UUID NOT NULL REFERENCES routing_decision(id) ON DELETE CASCADE,
  delivery_kind   delivery_kind NOT NULL,
  target_handle   TEXT,
  delivered_at    TIMESTAMPTZ,
  acknowledged_at TIMESTAMPTZ,
  response_at     TIMESTAMPTZ,
  error           TEXT,
  retry_count     INT NOT NULL DEFAULT 0,
  idempotency_key TEXT UNIQUE
);
CREATE INDEX delivery_log_routing_idx ON delivery_log (routing_id);

CREATE TABLE human_override (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id    UUID NOT NULL REFERENCES inbound_message(id) ON DELETE CASCADE,
  by_member_id  UUID NOT NULL REFERENCES team_member(id),
  override_kind override_reason NOT NULL,
  old_value     JSONB,
  new_value     JSONB,
  comment       TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX human_override_message_idx ON human_override (message_id);

CREATE TABLE audit_event (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL REFERENCES org(id) ON DELETE CASCADE,
  actor       TEXT NOT NULL,    -- 'system','member:<uuid>','automation:<name>'
  action      TEXT NOT NULL,    -- 'message.ingested','classification.created','override.applied',...
  entity_kind TEXT,
  entity_id   UUID,
  payload     JSONB,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX audit_event_entity_idx ON audit_event (entity_kind, entity_id);
CREATE INDEX audit_event_time_idx   ON audit_event (created_at DESC);

-- =====================================================================
-- PHASE 2  -  Vendor & Warranty Concierge
-- =====================================================================

CREATE TABLE vendor (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL REFERENCES org(id) ON DELETE CASCADE,
  display_name  TEXT NOT NULL,
  trades        TEXT[] NOT NULL,        -- ['plumbing','hvac','electrical','locksmith',...]
  counties      TEXT[] NOT NULL,
  rating        SMALLINT,
  emergency     BOOLEAN NOT NULL DEFAULT false,
  contact_email TEXT,
  contact_phone TEXT,
  notes         TEXT,
  active        BOOLEAN NOT NULL DEFAULT true,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX vendor_trades_idx   ON vendor USING gin (trades);
CREATE INDEX vendor_counties_idx ON vendor USING gin (counties);
CREATE INDEX vendor_active_idx   ON vendor (org_id) WHERE active = true;

CREATE TABLE warranty (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  property_id UUID NOT NULL REFERENCES property(id) ON DELETE CASCADE,
  item        TEXT NOT NULL,
  starts_on   DATE,
  ends_on     DATE,
  provider    TEXT,
  policy_id   TEXT,
  doc_url     TEXT
);
CREATE INDEX warranty_property_idx ON warranty (property_id);

CREATE TABLE emergency_playbook (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL REFERENCES org(id) ON DELETE CASCADE,
  scenario    TEXT NOT NULL,            -- 'flood','gas_leak','no_heat','lockout',...
  steps       JSONB NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE concierge_query (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id  UUID REFERENCES inbound_message(id) ON DELETE SET NULL,
  contact_id  UUID REFERENCES contact(id),
  property_id UUID REFERENCES property(id),
  query_text  TEXT NOT NULL,
  asked_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE concierge_response (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  query_id      UUID NOT NULL REFERENCES concierge_query(id) ON DELETE CASCADE,
  vendor_ids    UUID[],
  warranty_ids  UUID[],
  steps         JSONB,
  response_text TEXT,
  responded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =====================================================================
-- PHASE 3  -  Buyer Qualification Workflow
-- =====================================================================

CREATE TABLE buyer_profile (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  contact_id          UUID NOT NULL REFERENCES contact(id) ON DELETE CASCADE,
  financing           JSONB,                       -- {preapproved, lender, max_loan}
  preferred_counties  TEXT[],
  schools_pref        TEXT,
  commute_pref        JSONB,                       -- {anchor_address, max_minutes}
  price_min           NUMERIC(12,2),
  price_max           NUMERIC(12,2),
  bedrooms_min        SMALLINT,
  bathrooms_min       NUMERIC(3,1),
  property_kinds      TEXT[],
  notes               TEXT,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX buyer_profile_contact_idx ON buyer_profile (contact_id);

CREATE TABLE qualification_session (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  contact_id        UUID NOT NULL REFERENCES contact(id) ON DELETE CASCADE,
  buyer_profile_id  UUID REFERENCES buyer_profile(id),
  triggered_by      TEXT,                          -- 'manual','router_topic_buyer_lead','call_logged'
  started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at       TIMESTAMPTZ,
  fit_score         NUMERIC(4,3),
  brief_text        TEXT,
  status            TEXT NOT NULL DEFAULT 'in_progress'
);
CREATE INDEX qualification_session_contact_idx ON qualification_session (contact_id);

CREATE TABLE qualification_response (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id    UUID NOT NULL REFERENCES qualification_session(id) ON DELETE CASCADE,
  question_key  TEXT NOT NULL,
  answer        JSONB NOT NULL,
  answered_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =====================================================================
-- PHASE 4  -  Communication Digest
-- =====================================================================

CREATE TABLE thread (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL REFERENCES org(id) ON DELETE CASCADE,
  thread_key     TEXT,                              -- derived: contact + property + topic
  contact_id     UUID REFERENCES contact(id),
  property_id    UUID REFERENCES property(id),
  first_msg_at   TIMESTAMPTZ NOT NULL,
  last_msg_at    TIMESTAMPTZ NOT NULL,
  msg_count      INT NOT NULL DEFAULT 0,
  status         TEXT NOT NULL DEFAULT 'open'       -- 'open','dormant','closed'
);
CREATE INDEX thread_org_last_idx ON thread (org_id, last_msg_at DESC);
CREATE INDEX thread_contact_idx  ON thread (contact_id);

CREATE TABLE thread_message (
  thread_id  UUID NOT NULL REFERENCES thread(id) ON DELETE CASCADE,
  message_id UUID NOT NULL REFERENCES inbound_message(id) ON DELETE CASCADE,
  PRIMARY KEY (thread_id, message_id)
);

CREATE TABLE digest (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL REFERENCES org(id) ON DELETE CASCADE,
  thread_id    UUID REFERENCES thread(id),
  scope        TEXT NOT NULL,                       -- 'thread','daily','weekly'
  body_md      TEXT NOT NULL,
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE action_item (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  digest_id   UUID REFERENCES digest(id) ON DELETE CASCADE,
  message_id  UUID REFERENCES inbound_message(id),
  description TEXT NOT NULL,
  owner_id    UUID REFERENCES team_member(id),
  due_at      TIMESTAMPTZ,
  status      TEXT NOT NULL DEFAULT 'open',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX action_item_owner_idx ON action_item (owner_id, status);

-- =====================================================================
-- PHASE 5  -  Memory & Analytics
-- =====================================================================

CREATE TABLE metric_daily (
  org_id        UUID NOT NULL REFERENCES org(id) ON DELETE CASCADE,
  metric_date   DATE NOT NULL,
  metric_key    TEXT NOT NULL,
  dim_owner_id  UUID REFERENCES team_member(id),
  dim_topic     topic_code,
  dim_channel   channel_kind,
  dim_county    TEXT,
  value_num     NUMERIC(14,3),
  value_count   INT,
  PRIMARY KEY (org_id, metric_date, metric_key,
               COALESCE(dim_owner_id, '00000000-0000-0000-0000-000000000000'::uuid),
               COALESCE(dim_topic, 'unknown'::topic_code),
               COALESCE(dim_channel, 'manual'::channel_kind),
               COALESCE(dim_county, ''))
);

CREATE TABLE anomaly_alert (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL REFERENCES org(id) ON DELETE CASCADE,
  detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  metric_key  TEXT NOT NULL,
  severity    TEXT NOT NULL,
  description TEXT,
  payload     JSONB
);
CREATE INDEX anomaly_alert_org_time_idx ON anomaly_alert (org_id, detected_at DESC);

CREATE TABLE manual_time_entry (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL REFERENCES org(id) ON DELETE CASCADE,
  member_id   UUID NOT NULL REFERENCES team_member(id),
  message_id  UUID REFERENCES inbound_message(id),
  seconds     INT NOT NULL,
  reason      TEXT,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX manual_time_member_idx ON manual_time_entry (member_id, occurred_at DESC);

-- =====================================================================
-- Convenience VIEWS (for Phase 5 dashboards and Phase 4 digest queries)
-- =====================================================================

CREATE VIEW v_messages_with_routing AS
SELECT
  m.id  AS message_id, m.org_id, m.channel, m.received_at, m.contact_id,
  c.urgency, c.topic, c.is_business, c.summary,
  r.primary_owner_id, tm.display_name AS owner_name,
  r.is_archive, r.is_unsure
FROM inbound_message m
LEFT JOIN classification    c  ON c.message_id  = m.id
LEFT JOIN routing_decision  r  ON r.message_id  = m.id
LEFT JOIN team_member       tm ON tm.id          = r.primary_owner_id;

CREATE VIEW v_owner_load_daily AS
SELECT
  org_id, date_trunc('day', received_at)::date AS day,
  owner_name, urgency, count(*) AS n
FROM v_messages_with_routing
WHERE owner_name IS NOT NULL
GROUP BY 1,2,3,4;

CREATE VIEW v_override_rate_weekly AS
SELECT
  m.org_id, date_trunc('week', m.received_at)::date AS week,
  count(DISTINCT m.id) AS msgs,
  count(DISTINCT h.message_id) AS overrides,
  (count(DISTINCT h.message_id)::numeric / NULLIF(count(DISTINCT m.id),0)) AS override_rate
FROM inbound_message m
LEFT JOIN human_override h ON h.message_id = m.id
GROUP BY 1,2;
"""

with open(SQL, "w", encoding="utf-8") as f:
    f.write(SCHEMA_SQL)

# ===========================================================================
# ER diagram (CAD-style, consistent with the flow chart)
# ===========================================================================
plt.rcParams["font.family"] = "DejaVu Sans Mono"
plt.rcParams["font.size"] = 7
INK = "#0A0A0A"; GRID = "#D7DEE3"; BLUEPRINT = "#1F4E79"
PHASE_FILL = {
    1: "#E8F0F7",
    2: "#FFF4E0",
    3: "#EAF6EA",
    4: "#F2E8F6",
    5: "#FDEDED",
}

fig, ax = plt.subplots(figsize=(22, 14))
ax.set_xlim(0, 220); ax.set_ylim(0, 140); ax.set_aspect("equal"); ax.axis("off")

# Sheet
ax.add_patch(Rectangle((1.5,1.5), 217, 137, fill=False, lw=2.0, edgecolor=INK))
ax.add_patch(Rectangle((3.0,3.0), 214, 134, fill=False, lw=0.8, edgecolor=INK))
for x in range(5, 220, 5):
    ax.add_line(Line2D([x,x],[3,137], color=GRID, lw=0.3, zorder=0))
for y in range(5, 140, 5):
    ax.add_line(Line2D([3,217],[y,y], color=GRID, lw=0.3, zorder=0))

# Title
ax.text(110, 132, "DB SCHEMA  -  AI OPERATING PLATFORM (PHASE 1 -> 5)",
        ha="center", va="center", fontsize=15, fontweight="bold", color=BLUEPRINT)
ax.text(110, 128, "APTEKER REALTY  /  PROACTIVE REVENUE  /  REV 1.0  /  2026-05-17",
        ha="center", va="center", fontsize=8, color=INK)
ax.add_line(Line2D([6,214],[124,124], color=INK, lw=0.8))

# Table positions: (x,y,w,h, name, phase, columns_summary)
def t_box(x, y, name, cols, phase, w=32, h=None):
    h = h if h is not None else 6 + 2.4*len(cols)
    ax.add_patch(Rectangle((x,y),w,h, facecolor=PHASE_FILL[phase], edgecolor=INK, lw=1.0, zorder=2))
    ax.text(x+w/2, y+h-2.4, name, ha="center", va="center",
            fontsize=8.5, fontweight="bold", color=BLUEPRINT)
    ax.add_line(Line2D([x,x+w],[y+h-3.8, y+h-3.8], color=INK, lw=0.6))
    for i, c in enumerate(cols):
        ax.text(x+1, y+h-5.8-i*2.2, c, ha="left", va="center", fontsize=6.5, color=INK)
    return (x, y, w, h)

# --- Layout ---
# Sheet is 220 wide x 140 tall, title block at bottom, separator at y=124.
# Available content area: y = 6..122
#
# Five horizontal bands, top-to-bottom:
#   y=106..122  org / team / availability / property
#   y=82..100   contact / contact_identity / contact_property / channel_account / Phase 5 anomaly_alert
#   y=58..78    message + attachment + classification + routing + delivery + Phase 5 metric_daily
#   y=34..54    routing_rule_version / human_override / audit / thread / thread_message / digest / action_item
#   y=8..30     Phase 2 (vendor, warranty, emergency_playbook, concierge) / Phase 3 (buyer chain) /
#               Phase 5 (manual_time_entry)

# Top band ----------------------------------------------------------
org      = t_box(8,   106, "org",
                 ["id PK","name","timezone","retention_days"], 1, w=30)
team     = t_box(42,  110, "team_member",
                 ["id PK","org_id FK","display_name","role","(+more)"], 1, w=36)
tavail   = t_box(82,  110, "team_availability",
                 ["id PK","member_id FK","starts_at","ends_at","kind"], 1, w=34)
prop     = t_box(120, 110, "property",
                 ["id PK","org_id FK","external_id","county","(+more)"], 1, w=38)
aa       = t_box(162, 110, "anomaly_alert",
                 ["id PK","org_id FK","metric_key","severity","(+more)"], 5, w=50)

# Upper-mid band ----------------------------------------------------
contact  = t_box(8,   82, "contact",
                 ["id PK","org_id FK","display_name","is_vip","lifecycle",
                  "notes JSONB"], 1, w=30)
cident   = t_box(42,  82, "contact_identity",
                 ["id PK","contact_id FK","channel","identifier","verified"], 1, w=36)
cprop    = t_box(82,  82, "contact_property",
                 ["contact_id FK","property_id FK","relation"], 1, w=34)
chan     = t_box(120, 84, "channel_account",
                 ["id PK","org_id FK","channel","account_label",
                  "status","(+more)"], 1, w=38)
md       = t_box(162, 84, "metric_daily",
                 ["org_id PK","metric_date PK","metric_key PK",
                  "dim_owner_id","dim_topic","(+more)"], 5, w=50)

# Middle band ------------------------------------------------------
att      = t_box(8,   58, "message_attachment",
                 ["id PK","message_id FK","storage_url","mime_type",
                  "transcript","ocr_text"], 1, w=30)
msg      = t_box(42,  60, "inbound_message",
                 ["id PK","org_id FK","channel_account_id FK",
                  "channel","contact_id FK","body",
                  "received_at","(+more)"], 1, w=36)
clas     = t_box(82,  60, "classification",
                 ["id PK","message_id FK","urgency","topic",
                  "is_business","summary","cost_usd","(+more)"], 1, w=34)
rd       = t_box(120, 60, "routing_decision",
                 ["id PK","message_id FK","classification_id FK",
                  "rule_version_id FK","primary_owner_id FK",
                  "fallback_owner_id FK","(+more)"], 1, w=38)
deliv    = t_box(162, 60, "delivery_log",
                 ["id PK","routing_id FK","delivery_kind",
                  "delivered_at","retry_count",
                  "idempotency_key UQ"], 1, w=50)

# Lower-mid band --------------------------------------------------
rrv      = t_box(8,   34, "routing_rule_version",
                 ["id PK","org_id FK","version","yaml_source","yaml_hash",
                  "created_by"], 1, w=30)
over     = t_box(42,  34, "human_override",
                 ["id PK","message_id FK","by_member_id FK","override_kind",
                  "old_value","new_value","comment"], 1, w=36)
aud      = t_box(82,  34, "audit_event",
                 ["id PK","org_id FK","actor","action","entity_kind",
                  "entity_id","payload"], 1, w=34)
thread   = t_box(120, 34, "thread",
                 ["id PK","org_id FK","thread_key","contact_id FK",
                  "property_id FK","first_msg_at","last_msg_at",
                  "msg_count","status"], 4, w=22)
tmsg     = t_box(144, 34, "thread_message",
                 ["thread_id FK","message_id FK"], 4, w=18)
dig      = t_box(164, 34, "digest",
                 ["id PK","org_id FK","thread_id FK","scope","body_md"], 4, w=22)
ai       = t_box(188, 34, "action_item",
                 ["id PK","digest_id FK","message_id FK","description",
                  "owner_id FK","due_at","status"], 4, w=24)

# Bottom band ------------------------------------------------------
vendor   = t_box(8,    8, "vendor",
                 ["id PK","org_id FK","display_name","trades[]",
                  "counties[]","emergency","contact_phone"], 2, w=30)
warr     = t_box(42,   8, "warranty",
                 ["id PK","property_id FK","item","ends_on","provider",
                  "doc_url"], 2, w=24)
epb      = t_box(68,   8, "emergency_playbook",
                 ["id PK","org_id FK","scenario","steps JSONB"], 2, w=22)
cq       = t_box(92,   8, "concierge_query/response",
                 ["query_id","message_id FK","property_id FK",
                  "vendor_ids[]","warranty_ids[]","response_text"], 2, w=30)
bp       = t_box(124,  8, "buyer_profile",
                 ["id PK","contact_id FK","financing JSONB",
                  "preferred_counties[]","price_min","price_max",
                  "bedrooms_min","property_kinds[]"], 3, w=28)
qs       = t_box(154,  8, "qualification_session",
                 ["id PK","contact_id FK","buyer_profile_id FK",
                  "triggered_by","fit_score","brief_text","status"], 3, w=28)
qr       = t_box(184,  8, "qualification_response",
                 ["id PK","session_id FK","question_key",
                  "answer JSONB"], 3, w=26)
# manual_time_entry omitted from the diagram for layout clarity;
# it is documented in section 7 of the schema DOCX and lives in 02_schema.sql.
mt = None

def arr(b1, b2, label=None):
    x1, y1, w1, h1_ = b1
    x2, y2, w2, h2_ = b2
    sx, sy = x1 + w1/2, y1 + h1_/2
    tx, ty = x2 + w2/2, y2 + h2_/2
    a = FancyArrowPatch((sx,sy),(tx,ty), arrowstyle='-|>', mutation_scale=8,
                        lw=0.6, color=INK, zorder=1)
    ax.add_patch(a)
    if label:
        ax.text((sx+tx)/2,(sy+ty)/2+0.4, label, ha="center", va="bottom",
                fontsize=5.5, color=INK)

# Phase 1 FKs
arr(team, org)
arr(tavail, team)
arr(contact, org)
arr(cident, contact)
arr(prop, org)
arr(cprop, contact); arr(cprop, prop)
arr(chan, org)
arr(msg, chan); arr(msg, contact); arr(msg, org)
arr(att, msg)
arr(clas, msg)
arr(rrv, org)
arr(rd, msg); arr(rd, clas); arr(rd, rrv); arr(rd, team)
arr(deliv, rd)
arr(over, msg); arr(over, team)
arr(aud, org)
# Phase 2
arr(vendor, org); arr(warr, prop); arr(epb, org); arr(cq, msg)
# Phase 3
arr(bp, contact); arr(qs, contact); arr(qs, bp); arr(qr, qs)
# Phase 4
arr(thread, contact); arr(thread, prop); arr(thread, org); arr(tmsg, thread); arr(tmsg, msg)
arr(dig, thread); arr(ai, dig); arr(ai, team)
# Phase 5
arr(md, team); arr(aa, org)
if mt is not None:
    arr(mt, team); arr(mt, msg)

# Legend
LX, LY = 6, 6
ax.add_patch(Rectangle((LX,LY),60,10, fill=False, lw=1.0, edgecolor=INK))
ax.text(LX+1, LY+8, "LEGEND  -  table fill colour = phase", fontsize=7, fontweight="bold")
for i,(ph, lbl) in enumerate([(1,"Phase 1 Core"),(2,"Phase 2 Concierge"),
                              (3,"Phase 3 Buyer"),(4,"Phase 4 Digest"),(5,"Phase 5 Analytics")]):
    ax.add_patch(Rectangle((LX+1+i*12, LY+2), 3, 2.5, facecolor=PHASE_FILL[ph], edgecolor=INK))
    ax.text(LX+5+i*12, LY+3.2, lbl, fontsize=6.5)

# Title block
TBX, TBY, TBW, TBH = 150, 6, 64, 10
ax.add_patch(Rectangle((TBX,TBY),TBW,TBH, fill=False, lw=1.2, edgecolor=INK))
for gx in [TBX+22, TBX+43]:
    ax.add_line(Line2D([gx,gx],[TBY,TBY+TBH], color=INK, lw=0.5))
for gy in [TBY+3.3, TBY+6.6]:
    ax.add_line(Line2D([TBX,TBX+TBW],[gy,gy], color=INK, lw=0.5))
def tb(col,row,lbl,val,bold=False):
    x0 = TBX + col*22; y0 = TBY + row*3.3
    ax.text(x0+0.5, y0+2.4, lbl, fontsize=5.5, color="#555")
    ax.text(x0+0.5, y0+0.9, val, fontsize=7+(1 if bold else 0),
            fontweight="bold" if bold else "normal")
tb(0,2,"DRAWING NO.","APT-AIR-ER-001",True)
tb(1,2,"REV","1.0"); tb(2,2,"SHEET","1 OF 1")
tb(0,1,"PROJECT","AI Op Platform"); tb(1,1,"PHASES","1 -> 5"); tb(2,1,"SCALE","NTS")
tb(0,0,"DRAWN","ProActive Rev."); tb(1,0,"DATE","2026-05-17"); tb(2,0,"APPROVED","N. Apteker")

plt.savefig(PNG, dpi=220, bbox_inches="tight", facecolor="white")
plt.savefig(PDF, bbox_inches="tight", facecolor="white")
plt.close(fig)

# ===========================================================================
# DOCX
# ===========================================================================
d = new_doc()
title(d, "DB Schema  -  Forward-compatible (Phase 1 to Phase 5)")
body(d, "Apteker Realty  /  AI Operating Platform  /  Rev 1.0  /  2026-05-17")
body(d, "This document defines the persistent data model for the AI Issue Router "
        "and every downstream phase (Vendor Concierge, Buyer Qualification, "
        "Communication Digest, Memory & Dashboard). One database, one timeline, "
        "no migrations required when later phases come online.")

h1(d, "1. Design principles")
bullet(d, "ONE database (Postgres 14+), ONE timeline. Phase 1 creates the core tables; later phases only ADD tables that FK into Phase 1.")
bullet(d, "Multi-tenant from day one (every table has org_id) - costs nothing now and protects against a future multi-client deployment.")
bullet(d, "Every AI decision is reproducible: classification snapshots model name, version, prompt tokens, and cost; routing snapshots the matched rule_version_id.")
bullet(d, "Audit by default: audit_event captures every state change keyed by (entity_kind, entity_id).")
bullet(d, "Idempotency keys are first-class - delivery_log.idempotency_key UNIQUE prevents double-send on retries.")
bullet(d, "PII separation: body is the raw text; body_redacted is what we send to the LLM. Original is kept locally for the audit trail.")
bullet(d, "Flexible JSONB columns are reserved for areas that change quickly (buyer_profile.financing, property.metadata, channel_account.config_public). Everything else is strongly typed.")

h1(d, "2. ER diagram")
picture(d, PNG, 7.2,
        alt_text="Entity-relationship diagram of the AI Operating Platform database schema. "
                 "Phase 1 core tables (org, team_member, contact, contact_identity, property, "
                 "channel_account, inbound_message, message_attachment, classification, "
                 "routing_rule_version, routing_decision, delivery_log, human_override, audit_event) "
                 "are coloured pale blue. Phase 2 vendor/warranty tables are pale orange, "
                 "Phase 3 buyer-qualification tables are pale green, Phase 4 thread/digest/action_item "
                 "tables are pale lavender, Phase 5 analytics tables are pale red. Arrows show "
                 "foreign-key relationships radiating out from inbound_message and team_member as "
                 "the central hubs.",
        alt_title="DB schema ER diagram, phases 1-5")

h1(d, "3. Phase 1 core tables (built during Phase I)")
table(d, [
    ["Table",               "Purpose",                                              "Key FKs"],
    ["org",                  "Apteker Realty tenant row.",                            "-"],
    ["team_member",          "Nathalie, Fabi, Noa with role, counties, capacity.",     "org"],
    ["team_availability",    "PTO/sick/focus windows so routing skips unavailable.",   "team_member"],
    ["contact",              "Anyone we receive messages from.",                       "org"],
    ["contact_identity",     "Phone / email / handle aliases per contact.",            "contact"],
    ["property",             "Listings and managed properties.",                       "org"],
    ["contact_property",     "Many-to-many: tenant/owner/prospect.",                   "contact, property"],
    ["channel_account",      "Each monitored inbox (Apple Mail, WhatsApp Business, sms_phone, ...).", "org"],
    ["inbound_message",      "Normalized message record. Single source of truth.",     "org, channel_account, contact"],
    ["message_attachment",   "Voice/image/pdf attachments + transcripts.",             "inbound_message"],
    ["classification",       "AI output: urgency, topic, summary, draft reply, cost.", "inbound_message, property"],
    ["routing_rule_version", "Versioned YAML of routing rules (reproducibility).",     "org"],
    ["routing_decision",     "Which owner + rule fired + flags (archive/unsure).",     "inbound_message, classification, routing_rule_version, team_member"],
    ["delivery_log",         "Outbound notifications + ACK/response timestamps.",      "routing_decision"],
    ["human_override",       "When a person re-tags or re-routes; trains future tuning.","inbound_message, team_member"],
    ["audit_event",          "Generic event log for every state change.",              "org"],
], widths=[1.7, 3.8, 1.7])

h1(d, "4. Phase 2 additions (Vendor & Warranty Concierge)")
table(d, [
    ["Table",            "Purpose",                                             "Key FKs"],
    ["vendor",            "Vendor catalogue with trades + counties + emergency flag.", "org"],
    ["warranty",          "Per-property warranty terms.",                         "property"],
    ["emergency_playbook","Step-by-step guides keyed by scenario.",               "org"],
    ["concierge_query",   "Each concierge invocation (also linked to inbound_message when triggered by the Router).", "inbound_message, contact, property"],
    ["concierge_response","Vendor + warranty IDs + steps returned.",              "concierge_query"],
], widths=[1.7, 4.0, 1.5])

h1(d, "5. Phase 3 additions (Buyer Qualification Workflow)")
table(d, [
    ["Table",                 "Purpose",                                          "Key FKs"],
    ["buyer_profile",          "Persistent constraints per buyer (financing, areas, beds, ...).", "contact"],
    ["qualification_session",  "One workflow run with fit_score + brief_text.",      "contact, buyer_profile"],
    ["qualification_response", "Per-question answers (form schema lives in code).",  "qualification_session"],
], widths=[1.7, 4.0, 1.5])
body(d, "Trigger: when the Router classifies a message as topic='buyer_lead' AND "
        "is_follow_up=true (signaling first call has already happened), a new "
        "qualification_session is opened and Nathalie sees the form pre-filled "
        "with whatever the buyer has already said.")

h1(d, "6. Phase 4 additions (Communication Digest)")
table(d, [
    ["Table",         "Purpose",                                             "Key FKs"],
    ["thread",         "Groups inbound messages by (contact, property, topic).", "org, contact, property"],
    ["thread_message", "Join table.",                                            "thread, inbound_message"],
    ["digest",         "Generated summary text + scope (thread/daily/weekly).",  "org, thread"],
    ["action_item",    "Extracted to-dos with owner + due date.",                "digest, inbound_message, team_member"],
], widths=[1.6, 4.0, 1.5])

h1(d, "7. Phase 5 additions (Memory & Analytics Dashboard)")
table(d, [
    ["Table",            "Purpose",                                                 "Notes"],
    ["metric_daily",      "Pre-aggregated per (date, key, owner/topic/channel/county).","Refreshed by nightly cron from Phase 1 raw tables"],
    ["anomaly_alert",     "Anomaly detection output (e.g. P0 spike).",                  ""],
    ["manual_time_entry", "Where humans STILL spent time despite the AI.",              "Sourced from human_override + manual stopwatch entries"],
], widths=[1.7, 4.0, 1.5])

h1(d, "8. Convenience views")
bullet(d, "v_messages_with_routing  -  flat row per message with classification + owner.")
bullet(d, "v_owner_load_daily       -  count of messages per owner per urgency per day.")
bullet(d, "v_override_rate_weekly   -  weekly override rate as a routing-accuracy proxy.")

h1(d, "9. Why this schema is forward-compatible")
bullet(d, "Phase 4 (Digest) does NOT need to re-process Phase 1 messages; it just builds thread groups on top of inbound_message and writes summaries to digest. No data migration.")
bullet(d, "Phase 5 (Dashboard) reads ONLY from views and metric_daily; no need to scan raw messages at query time. Anomaly detection works off the same materialized aggregates.")
bullet(d, "Phase 3 (Buyer Qualification) FKs into contact, so qualification sessions are automatically joined to all prior inbound messages from the same buyer.")
bullet(d, "Phase 2 (Vendor Concierge) can be triggered FROM the Router (concierge_query.message_id) or invoked standalone (message_id NULL).")

h1(d, "10. Operational notes")
bullet(d, "Storage growth at 5k msgs/month: ~200 MB/year including JSONB payloads. A $7/mo Postgres plan covers Phase 1 with headroom for ~3 years.")
bullet(d, "Backups: daily logical dump (pg_dump) + 7-day point-in-time recovery on the hosted Postgres plan.")
bullet(d, "Data retention: nightly job deletes inbound_message rows older than org.retention_days. Aggregates in metric_daily are kept indefinitely.")
bullet(d, "Encryption: TLS in transit; AES-256 at rest from the Postgres provider. body and body_redacted are NOT separately encrypted (the entire DB is).")
bullet(d, "Secrets: channel_account.config_secret_ref is a pointer (e.g. 'render://secret/twilio_token') not the secret itself.")

h1(d, "11. Drop-in SQL")
body(d, "The full DDL is in the sibling file 02_schema.sql (~280 lines). Apply with:")
code(d, "psql $DATABASE_URL -f 02_schema.sql")
body(d, "The script is idempotent-friendly when run on a fresh database; it does not include DROP statements - that is intentional, to avoid accidental data loss during deployment.")

d.save(DOCX)
print("Wrote:", SQL)
print("Wrote:", PNG)
print("Wrote:", PDF)
print("Wrote:", DOCX)
