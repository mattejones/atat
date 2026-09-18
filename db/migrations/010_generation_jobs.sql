-- Migration 010: generation jobs — async generative work, with a reviewable draft stage
--
-- Every generative request (analyse_job, generate_cv, run_coverage, run_judges,
-- regenerate_section, generate_cover_letter, generate_answers, submit_job) becomes a
-- row here. The row id is the reference key handed back to the caller immediately;
-- the work runs on a background worker and the caller checks back with get_job.
--
-- A draft is the same row at status 'draft': the request is assembled (inputs + a
-- preview of the prompt) but nothing has been sent to a model. A human or agent can
-- edit the inputs, then submit it — at which point it moves to 'queued'.
--
--   draft ──submit──▶ queued ──▶ running ──▶ succeeded | failed
--     │                  │          │
--     └──── cancel ──────┴──────────┴──▶ cancelled
--
-- Cancelling a running job sets cancel_requested; the model call in flight still
-- completes (the tokens are spent) but its output is discarded before any write.

CREATE TABLE IF NOT EXISTS generation_jobs (
    id                TEXT PRIMARY KEY,
    kind              TEXT NOT NULL,             -- analyse_job | generate_cv | ...
    status            TEXT NOT NULL,             -- draft | queued | running | succeeded | failed | cancelled

    -- What the job acts on. application_id is NULL for intake kinds (analyse_job,
    -- submit_job) until the job creates the application. target_id is the report id
    -- for section-level kinds (run_judges, regenerate_section), NULL otherwise.
    application_id    TEXT REFERENCES applications(id) ON DELETE CASCADE,
    target_id         TEXT,

    params            TEXT NOT NULL DEFAULT '{}',  -- JSON: the editable inputs
    version           INTEGER NOT NULL DEFAULT 1,  -- bumped on every draft edit (optimistic concurrency)

    -- The prompt as assembled at submission, with unchanged cv-library content elided.
    -- Kept for audit and prompt tuning: what was actually asked, not what we think was.
    prompt_snapshot   TEXT,

    result            TEXT,                        -- JSON, on success
    error             TEXT,                        -- on failure
    cancel_requested  INTEGER NOT NULL DEFAULT 0,

    created_by        TEXT NOT NULL,               -- agent | human
    submitted_by      TEXT,                        -- agent | human

    -- Worker bookkeeping. A running job whose heartbeat goes stale belonged to a
    -- process that died mid-run; it is marked failed, not silently re-run, because a
    -- re-run spends tokens again and the first attempt may have half-written output.
    worker_id         TEXT,
    heartbeat_at      DATETIME,

    created_at        DATETIME NOT NULL,
    updated_at        DATETIME NOT NULL,
    submitted_at      DATETIME,
    started_at        DATETIME,
    finished_at       DATETIME
);

CREATE INDEX IF NOT EXISTS idx_generation_jobs_status      ON generation_jobs(status);
CREATE INDEX IF NOT EXISTS idx_generation_jobs_application ON generation_jobs(application_id);
CREATE INDEX IF NOT EXISTS idx_generation_jobs_active      ON generation_jobs(kind, application_id, target_id, status);
