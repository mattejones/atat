-- Migration 009: JD spec — structured job-ad extraction and coverage judging
--
-- Introduces the jd_spec: a structured, verbatim-quoted extraction of a job ad's
-- requirements, anti-patterns and signals, mapped against cv-library evidence.
--
-- Rationale. Tiers 1 and 2 both validate BACKWARDS: tier 1 checks prose mechanics,
-- tier 2 checks claims against the library. Nothing validated FORWARDS against the
-- job ad itself. A CV could be mechanically clean and factually accurate while being
-- aimed at entirely the wrong target — and pass every judge. The jd_spec closes that
-- gap by making the job ad a first-class, reviewable artefact rather than freehand
-- prose in generation_notes.
--
-- The same artefact does three jobs:
--   1. constrains generation (the tailorer writes to a spec, not to vibes)
--   2. becomes the tier 3 coverage judge's rubric for free
--   3. computes a tier from must-have evidence coverage, before a generation is spent
--
-- New status: 'analysed' — spec extracted, awaiting human review, no CV generated yet.
-- New evaluation tier: 'coverage' — document-level, not per-section.
-- New flag types: 'coverage_gap' (a requirement no CV text addresses)
--                 'anti_pattern'  (CV text that trips a stated disqualifier)

-- ── jd_spec on applications ────────────────────────────────────────────────────
-- Stored as a JSON document. Shape:
--   {
--     "requirements":  [{"id","quote","must_have","evidence":[ref,...] | []}],
--     "anti_patterns": [{"id","quote"}],
--     "signals":       [{"id","quote"}],
--     "computed_tier": "T1|T2|T3",
--     "extracted_at":  iso8601
--   }
-- Every quote MUST be verbatim from jd_text. Every evidence ref MUST resolve to a
-- cv-library heading path (file.md#Heading). Both are validated on write — an
-- unquotable requirement does not exist, and an unresolvable ref is a hallucination.
ALTER TABLE applications ADD COLUMN jd_spec TEXT;

-- Timestamp of the last successful spec extraction / edit.
ALTER TABLE applications ADD COLUMN jd_spec_updated_at DATETIME;

-- ── Document-level evaluations ─────────────────────────────────────────────────
-- evaluations.report_id is NOT NULL and FKs to reports, but coverage is a property
-- of the whole document, not a section. Rather than weaken that constraint, coverage
-- results get their own table keyed on the application.
CREATE TABLE IF NOT EXISTS coverage_evaluations (
    id                  TEXT PRIMARY KEY,
    application_id      TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    passed              INTEGER NOT NULL DEFAULT 0,
    covered_count       INTEGER NOT NULL DEFAULT 0,
    partial_count       INTEGER NOT NULL DEFAULT 0,
    absent_count        INTEGER NOT NULL DEFAULT 0,
    violation_count     INTEGER NOT NULL DEFAULT 0,
    model               TEXT,
    prompt_tokens       INTEGER,
    completion_tokens   INTEGER,
    created_at          DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- ── Coverage findings ──────────────────────────────────────────────────────────
-- One row per requirement assessed, plus one row per anti-pattern violation.
-- kind='coverage'      -> ref_id is a requirement id,  status in (covered|partial|absent)
-- kind='anti_pattern'  -> ref_id is an anti_pattern id, status is always 'violated'
-- excerpt is verbatim CV text (empty for 'absent' — there is nothing to quote).
CREATE TABLE IF NOT EXISTS coverage_findings (
    id                  TEXT PRIMARY KEY,
    evaluation_id       TEXT NOT NULL REFERENCES coverage_evaluations(id) ON DELETE CASCADE,
    kind                TEXT NOT NULL,          -- coverage | anti_pattern
    ref_id              TEXT NOT NULL,          -- requirement id or anti_pattern id
    quote               TEXT NOT NULL,          -- the JD quote being assessed
    status              TEXT NOT NULL,          -- covered | partial | absent | violated
    excerpt             TEXT,                   -- verbatim CV text, if any
    reason              TEXT,
    created_at          DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- ── Round-trip (tier 0) failures ───────────────────────────────────────────────
-- Asserts that everything written into cv.md survives parse_cv into ParsedCV.
-- This tier exists because a regex in parse_cv silently truncated education year
-- ranges ("2014 - 2016" -> "2014") for months, and three tiers of sophisticated
-- judging never noticed: they all read the markdown, never the parsed result.
CREATE TABLE IF NOT EXISTS roundtrip_failures (
    id                  TEXT PRIMARY KEY,
    application_id      TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    field               TEXT NOT NULL,          -- e.g. education.years, experience.dates
    expected            TEXT NOT NULL,          -- what cv.md contains
    actual              TEXT,                   -- what parse_cv produced
    created_at          DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- ── Indexes ────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_coverage_evaluations_application_id
    ON coverage_evaluations(application_id);
CREATE INDEX IF NOT EXISTS idx_coverage_findings_evaluation_id
    ON coverage_findings(evaluation_id);
CREATE INDEX IF NOT EXISTS idx_roundtrip_failures_application_id
    ON roundtrip_failures(application_id);
