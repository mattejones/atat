-- 012_outcome_flags.sql — per-application flag for outcome reporting.
--
-- outside_inbox = 1 marks an application made through a channel this Gmail inbox
-- doesn't see (LinkedIn Easy Apply, an agency, another address). Outcome reports
-- (pipeline/insights/outcomes.py) exclude it, because its email history can't be
-- trusted to be complete. Outcomes themselves are derived on read, never stored.

ALTER TABLE applications ADD COLUMN outside_inbox INTEGER NOT NULL DEFAULT 0;
