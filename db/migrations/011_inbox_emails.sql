-- 011_inbox_emails.sql — Gmail inbox sync and the email review queue.
--
-- Replaces the placeholder application_emails table from 001, which was never written
-- to: its application_id was NOT NULL, so it couldn't hold an email that hasn't been
-- matched to an application yet — and matching is exactly what the review queue is for.
--
-- Every message the sync looks at gets a row, keyed by gmail_message_id, so a re-sync
-- never reprocesses it. Emails triaged as not job-related are kept as 'auto_ignored'
-- with headers only (no body) — enough to dedupe and to audit what was skipped.
--
-- Nothing here changes an application on its own. A row's proposed_* fields are what
-- the classifier suggests; they're applied only when the row is accepted.

DROP TABLE IF EXISTS application_emails;

CREATE TABLE IF NOT EXISTS inbox_emails (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    gmail_message_id    TEXT NOT NULL UNIQUE,
    gmail_thread_id     TEXT,
    sender              TEXT,
    sender_domain       TEXT,
    subject             TEXT,
    received_at         DATETIME,
    snippet             TEXT,
    body_excerpt        TEXT,               -- plain text, trimmed; NULL for auto_ignored

    classification      TEXT,               -- acknowledgement | rejection | interview_request |
                                            -- assessment | offer | recruiter_outreach |
                                            -- other_job | not_job
    confidence          REAL,               -- 0..1, the classifier's confidence in the match

    application_id      TEXT REFERENCES applications(id) ON DELETE SET NULL,
    match_reason        TEXT,               -- why this application: thread / domain / model
    evidence            TEXT,               -- short verbatim quote supporting the classification

    proposed_status     TEXT,               -- status to move to on accept, NULL = link only
    proposed_date_type  TEXT,               -- application_dates.date_type to log on accept
    proposed_date       TEXT,               -- ISO date (or datetime for interviews)
    extracted_json      TEXT,               -- everything else the classifier extracted

    review_status       TEXT NOT NULL DEFAULT 'pending',
                                            -- pending | accepted | dismissed | auto_ignored
    reviewed_at         DATETIME,
    model               TEXT,
    created_at          DATETIME NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_inbox_review_status ON inbox_emails(review_status);
CREATE INDEX IF NOT EXISTS idx_inbox_application   ON inbox_emails(application_id);
CREATE INDEX IF NOT EXISTS idx_inbox_thread        ON inbox_emails(gmail_thread_id);

-- Single-row sync bookkeeping. last_internal_date is Gmail's internalDate (ms since
-- epoch) of the newest message fully processed — the next sync starts from there.
CREATE TABLE IF NOT EXISTS gmail_sync_state (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    last_internal_date  INTEGER,
    last_synced_at      DATETIME,
    last_error          TEXT,
    last_result         TEXT                -- JSON counts from the last successful sync
);

INSERT OR IGNORE INTO gmail_sync_state (id) VALUES (1);
