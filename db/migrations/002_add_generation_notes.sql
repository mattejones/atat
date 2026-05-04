-- Migration 002: Add generation_notes to applications
-- Stores applicant notes passed to the LLM at generation time.

ALTER TABLE applications ADD COLUMN generation_notes TEXT;
