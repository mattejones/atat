-- Migration 003: Add role detail fields to applications
-- Location, work arrangement, and salary information.

ALTER TABLE applications ADD COLUMN location         TEXT;
ALTER TABLE applications ADD COLUMN work_arrangement TEXT;  -- remote | hybrid | office
ALTER TABLE applications ADD COLUMN hybrid_days      INTEGER; -- days/week in office (hybrid only)
ALTER TABLE applications ADD COLUMN salary_min       INTEGER;
ALTER TABLE applications ADD COLUMN salary_max       INTEGER;
ALTER TABLE applications ADD COLUMN salary_currency  TEXT DEFAULT 'GBP';
