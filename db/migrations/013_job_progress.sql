-- 013_job_progress.sql — what a running job is doing right now.
--
-- A full CV generation takes minutes with nothing to show for it. A job can now report its
-- current stage ("Writing the CV", "Checking the layout", ...), which the web UI shows instead
-- of guessing from a timer. Free text, replaced as the job moves on; NULL until the job says.

ALTER TABLE generation_jobs ADD COLUMN progress TEXT;
