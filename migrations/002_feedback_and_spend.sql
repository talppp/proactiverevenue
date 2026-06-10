-- Phase 2 alignment: feedback loop + LLM budget enforcement.
--
-- sms_feedback: Nathalie's corrections (wrong topic/urgency/owner) become
--   labeled eval data for tuning the classifier. A second brain that can't
--   record its operator's corrections can't improve.
-- llm_spend:   daily LLM spend accumulator so LLM_DAILY_USD_CEILING is
--   enforced at runtime, not just configured.

CREATE TABLE IF NOT EXISTS sms_feedback (
    id                BIGSERIAL PRIMARY KEY,
    msg_id            TEXT NOT NULL REFERENCES sms_inbox_raw (msg_id),
    corrected_topic   TEXT,
    corrected_urgency TEXT,
    note              TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS sms_feedback_msg_idx ON sms_feedback (msg_id);

CREATE TABLE IF NOT EXISTS llm_spend (
    day  DATE PRIMARY KEY,
    usd  NUMERIC(10, 5) NOT NULL DEFAULT 0
);

-- Contact memory needs fast per-sender history lookups.
CREATE INDEX IF NOT EXISTS sms_inbox_raw_sender_idx
    ON sms_inbox_raw (from_number, received_at DESC);
