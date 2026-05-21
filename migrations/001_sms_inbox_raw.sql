-- L1 SMS phone ingestion: staging table for messages forwarded from
-- Nathalie's iPhone via the Apple Shortcuts automation.
--
-- The webhook writes here idempotently (deterministic msg_id), the
-- in-process BackgroundTask hands off to L2, and the recovery loop drains
-- anything stuck. See docs/L1_SMS_Phone_Ingestion_Design.md.

CREATE TABLE IF NOT EXISTS sms_inbox_raw (
    msg_id            TEXT PRIMARY KEY,
    from_number       TEXT NOT NULL,
    to_number         TEXT,
    body              TEXT NOT NULL,
    received_at_phone TIMESTAMPTZ NOT NULL,
    received_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    status            TEXT NOT NULL DEFAULT 'new'
                          CHECK (status IN ('new', 'handed_off', 'dead_letter')),
    attempts          INT  NOT NULL DEFAULT 0,
    last_error        TEXT,
    handed_off_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS sms_inbox_raw_status_idx
    ON sms_inbox_raw (status, received_at)
    WHERE status = 'new';

CREATE INDEX IF NOT EXISTS sms_inbox_raw_dead_letter_idx
    ON sms_inbox_raw (received_at)
    WHERE status = 'dead_letter';
