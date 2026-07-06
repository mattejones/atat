-- Migration 008: Cover letters
--
-- One cover letter per application. Stores the markdown content, the user inputs
-- that drove generation, and the output of the research phase.
--
-- Note on research_brief: intentionally denormalised here. Company research is a
-- candidate for promotion to a top-level application-level table in a future
-- migration — the research.py module is already isolated in anticipation of that.

CREATE TABLE IF NOT EXISTS cover_letters (
    id                TEXT PRIMARY KEY,
    application_id    TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    markdown          TEXT,                              -- source of truth; mirrored to cover_letter.md
    status            TEXT NOT NULL DEFAULT 'draft',    -- draft | generated | edited
    research_company  INTEGER NOT NULL DEFAULT 1,       -- user toggle (0/1)
    research_role     INTEGER NOT NULL DEFAULT 1,       -- user toggle (0/1)
    draft_input       TEXT,                             -- optional rough draft supplied by user
    key_points        TEXT,                             -- optional bullet points supplied by user
    research_brief    TEXT,                             -- combined markdown output of research phase
    model             TEXT,
    generated_at      DATETIME,
    created_at        DATETIME NOT NULL DEFAULT (datetime('now')),
    updated_at        DATETIME NOT NULL DEFAULT (datetime('now')),

    UNIQUE(application_id)                              -- one cover letter per application
);

CREATE INDEX IF NOT EXISTS idx_cover_letters_application_id
    ON cover_letters(application_id);

CREATE TRIGGER IF NOT EXISTS cover_letters_updated_at
    AFTER UPDATE ON cover_letters
    FOR EACH ROW
    BEGIN
        UPDATE cover_letters SET updated_at = datetime('now') WHERE id = NEW.id;
    END;
