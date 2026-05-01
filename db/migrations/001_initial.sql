-- Migration 001: Initial schema
-- ATAT Application Tracking and Automation Tool

-- ── Schema version tracking ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- ── Core applications ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS applications (
    id              TEXT PRIMARY KEY,   -- slug: 2026-04-28_stripe_revops-manager
    company         TEXT NOT NULL,
    role            TEXT NOT NULL,
    source_url      TEXT,               -- link to the original job posting
    jd_text         TEXT,               -- full JD content
    cv_markdown     TEXT,               -- current CV content (source of truth)
    persona         TEXT,               -- which persona was selected by LLM
    tier            TEXT,               -- T1 / T2 / T3 / EX1
    status          TEXT NOT NULL DEFAULT 'generated',
                                        -- generated | reviewing | applied |
                                        -- acknowledged | interviewing |
                                        -- offered | rejected | excluded
    output_dir      TEXT,               -- filesystem path to artefacts
    has_pdf         INTEGER NOT NULL DEFAULT 0,
    model           TEXT,
    provider        TEXT,
    notes           TEXT,               -- freetext user notes
    created_at      DATETIME NOT NULL DEFAULT (datetime('now')),
    updated_at      DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- ── Application event log ──────────────────────────────────────────────────────
-- Every status change and notable action is recorded here.
-- Provides a full audit trail and feeds the timeline UI.
CREATE TABLE IF NOT EXISTS application_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id  TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    event_type      TEXT NOT NULL,      -- status_change | cv_edited | pdf_rendered |
                                        -- email_received | note_added
    from_status     TEXT,               -- for status_change events
    to_status       TEXT,               -- for status_change events
    detail          TEXT,               -- freetext: email subject, note content etc
    occurred_at     DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- ── Important dates ────────────────────────────────────────────────────────────
-- Structured date records for easy querying and display.
CREATE TABLE IF NOT EXISTS application_dates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id  TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    date_type       TEXT NOT NULL,      -- applied | interview | offer_received |
                                        -- offer_deadline | rejected
    date            DATE NOT NULL,
    notes           TEXT
);

-- ── Inbound email tracking ─────────────────────────────────────────────────────
-- For future Gmail integration. Records matched inbound emails per application.
CREATE TABLE IF NOT EXISTS application_emails (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id      TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    gmail_message_id    TEXT UNIQUE,    -- for deduplication
    sender              TEXT,
    subject             TEXT,
    received_at         DATETIME,
    classification      TEXT,           -- acknowledgement | rejection |
                                        -- interview_request | offer | other
    snippet             TEXT,           -- first ~200 chars for display
    processed           INTEGER NOT NULL DEFAULT 0
);

-- ── Exclusion feedback ─────────────────────────────────────────────────────────
-- EX1 excluded roles with reasons — for tuning upstream filters over time.
CREATE TABLE IF NOT EXISTS exclusion_feedback (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id      TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    reason              TEXT,
    filter_suggestion   TEXT,           -- what filter would have caught this
    created_at          DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- ── Indexes ────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_applications_status     ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_created_at ON applications(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_application_id   ON application_events(application_id);
CREATE INDEX IF NOT EXISTS idx_dates_application_id    ON application_dates(application_id);
CREATE INDEX IF NOT EXISTS idx_emails_application_id   ON application_emails(application_id);

-- ── Trigger: keep updated_at current ──────────────────────────────────────────
CREATE TRIGGER IF NOT EXISTS applications_updated_at
    AFTER UPDATE ON applications
    FOR EACH ROW
    BEGIN
        UPDATE applications SET updated_at = datetime('now') WHERE id = NEW.id;
    END;
