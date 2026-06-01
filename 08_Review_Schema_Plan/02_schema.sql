-- =====================================================================
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
