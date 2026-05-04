-- Migration 005: Add CV section versioning and judge/review pipeline
--
-- Introduces four new tables:
--   sections    — one row per CV section per application; tracks the accepted report
--   reports     — versioned generation attempts per section; forms a linked chain
--   evaluations — one row per judge tier run against a report
--   flags       — individual issues raised by an evaluation, with position spans
--
-- cv_markdown on applications remains as a composed cache of accepted sections.
-- It is recomposed and updated whenever a section report is accepted.

-- ── Sections ───────────────────────────────────────────────────────────────────
-- One row per section per application.
-- accepted_report_id is NULL until the user accepts a report for that section.
-- Until accepted, the initial generated report is used for composition.
CREATE TABLE IF NOT EXISTS sections (
    id                  TEXT PRIMARY KEY,
    application_id      TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    section_name        TEXT NOT NULL,          -- profile | experience | skills |
                                                -- education | certifications
    accepted_report_id  TEXT,                   -- FK to reports — set on accept
    created_at          DATETIME NOT NULL DEFAULT (datetime('now')),
    updated_at          DATETIME NOT NULL DEFAULT (datetime('now')),

    UNIQUE(application_id, section_name)        -- one section row per app per section
);

-- ── Reports ────────────────────────────────────────────────────────────────────
-- One row per generation attempt per section.
-- parent_report_id forms the linked chain — NULL on the first attempt.
-- file_path points to output/{app_id}/sections/{section_name}/{report_id}.md
CREATE TABLE IF NOT EXISTS reports (
    id                  TEXT PRIMARY KEY,
    application_id      TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    section_id          TEXT NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    parent_report_id    TEXT REFERENCES reports(id),
    section_name        TEXT NOT NULL,          -- denormalised for query convenience
    attempt             INTEGER NOT NULL DEFAULT 1,
    file_path           TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
                                                -- pending | accepted | rejected
    global_comment      TEXT,                   -- user-level feedback before retry
    formatted_prompt    TEXT,                   -- retry constraint block (NULL on attempt 1)
    escalated           INTEGER NOT NULL DEFAULT 0,
    escalation_reason   TEXT,                   -- retry_threshold | confidence_failure
    created_at          DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- Only one accepted report per section per application
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_accepted_per_section
ON reports(application_id, section_name)
WHERE status = 'accepted';

-- ── Evaluations ────────────────────────────────────────────────────────────────
-- One row per judge tier run against a report.
-- A report may have multiple evaluations (one per tier).
CREATE TABLE IF NOT EXISTS evaluations (
    id                  TEXT PRIMARY KEY,
    report_id           TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    tier                TEXT NOT NULL,          -- deterministic | cheap_llm | llm
    passed              INTEGER NOT NULL DEFAULT 0,
    flesch_score        REAL,                   -- populated by deterministic tier
    model               TEXT,                   -- populated by LLM tiers
    prompt_tokens       INTEGER,
    completion_tokens   INTEGER,
    created_at          DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- ── Flags ──────────────────────────────────────────────────────────────────────
-- One row per issue raised by an evaluation.
-- start_pos / end_pos are character offsets into the report's raw text.
-- excerpt is stored redundantly for prompt construction and audit.
CREATE TABLE IF NOT EXISTS flags (
    id                  TEXT PRIMARY KEY,
    evaluation_id       TEXT NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
    type                TEXT NOT NULL,          -- hotword | sentence_length |
                                                -- readability | accuracy | ai_texture
    start_pos           INTEGER NOT NULL,
    end_pos             INTEGER NOT NULL,
    excerpt             TEXT NOT NULL,
    message             TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'active',
                                                -- active | dismissed | actioned
    user_comment        TEXT,
    dismissal_reason    TEXT,
    created_at          DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- ── Indexes ────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_sections_application_id
    ON sections(application_id);

CREATE INDEX IF NOT EXISTS idx_reports_section_id
    ON reports(section_id);

CREATE INDEX IF NOT EXISTS idx_reports_parent_report_id
    ON reports(parent_report_id);

CREATE INDEX IF NOT EXISTS idx_evaluations_report_id
    ON evaluations(report_id);

CREATE INDEX IF NOT EXISTS idx_flags_evaluation_id
    ON flags(evaluation_id);

-- ── Trigger: keep sections.updated_at current ─────────────────────────────────
CREATE TRIGGER IF NOT EXISTS sections_updated_at
    AFTER UPDATE ON sections
    FOR EACH ROW
    BEGIN
        UPDATE sections SET updated_at = datetime('now') WHERE id = NEW.id;
    END;
