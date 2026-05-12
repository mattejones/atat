-- Migration 006: Application Questions Handler
--
-- Adds support for recording, generating, and refining answers to job
-- application questions on a per-application basis.
--
-- New tables:
--   application_questions — questions entered by the user per application
--   application_answers   — LLM-generated (and user-edited) answers
--   answer_feedback       — thumbs-up/down feedback; distilled into prompt signals
--
-- New column on applications:
--   qa_tone  — tone preference for this application's question answers
--              (professional | direct | conversational | technical)

-- ── Tone column on applications ────────────────────────────────────────────────
-- Added here so tone is per-application and read at generation time.
-- DEFAULT 'professional' applies retroactively to existing rows via SQLite semantics.
ALTER TABLE applications ADD COLUMN qa_tone TEXT NOT NULL DEFAULT 'professional';

-- ── Questions ──────────────────────────────────────────────────────────────────
-- One row per question per application. Questions are ordered by sort_order.
-- Users may add, edit, reorder, and delete questions at any time.
CREATE TABLE IF NOT EXISTS application_questions (
    id              TEXT PRIMARY KEY,
    application_id  TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    question_text   TEXT NOT NULL,
    response_length TEXT NOT NULL DEFAULT 'short',  -- short | paragraph
    needs_research  INTEGER NOT NULL DEFAULT 0,     -- 0 = no, 1 = include web search
    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_at      DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- ── Answers ────────────────────────────────────────────────────────────────────
-- One row per generation run per question. Multiple rows may exist per question
-- (e.g. after regeneration). The most recent row is always the live answer.
-- user_answer is NULL until the user edits; the effective answer is:
--   COALESCE(user_answer, ai_answer)
CREATE TABLE IF NOT EXISTS application_answers (
    id              TEXT PRIMARY KEY,
    question_id     TEXT NOT NULL REFERENCES application_questions(id) ON DELETE CASCADE,
    application_id  TEXT NOT NULL,                  -- denormalised for query convenience
    ai_answer       TEXT NOT NULL,
    user_answer     TEXT,                            -- NULL = use ai_answer as-is
    model_used      TEXT,
    created_at      DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- ── Feedback ───────────────────────────────────────────────────────────────────
-- Thumbs-up/down feedback on a specific answer.
-- processed_signal is populated asynchronously by the Haiku feedback processor.
-- processed = 0 means the distillation task has not yet run (or was dropped).
-- A startup recovery check should re-queue rows where processed = 0
-- and created_at < NOW() - 5 minutes.
CREATE TABLE IF NOT EXISTS answer_feedback (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    answer_id        TEXT NOT NULL REFERENCES application_answers(id) ON DELETE CASCADE,
    rating           TEXT NOT NULL,                 -- positive | negative
    user_comment     TEXT,
    processed_signal TEXT,                          -- distilled prompt signal
    processed        INTEGER NOT NULL DEFAULT 0,
    created_at       DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- ── Indexes ────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_questions_application_id
    ON application_questions(application_id);

CREATE INDEX IF NOT EXISTS idx_questions_sort_order
    ON application_questions(application_id, sort_order);

CREATE INDEX IF NOT EXISTS idx_answers_question_id
    ON application_answers(question_id);

CREATE INDEX IF NOT EXISTS idx_answers_application_id
    ON application_answers(application_id);

-- Used by startup recovery check and signal fetcher
CREATE INDEX IF NOT EXISTS idx_feedback_processed
    ON answer_feedback(processed);

CREATE INDEX IF NOT EXISTS idx_feedback_answer_id
    ON answer_feedback(answer_id);
