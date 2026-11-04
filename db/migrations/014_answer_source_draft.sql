-- 014_answer_source_draft.sql — keep the person's own wording when the AI improves it.
--
-- A person can write their own answer to an application question and ask for it to be improved.
-- The improved text is stored as the new live answer (ai_answer); the text it was improved FROM is
-- kept here, so "keep mine" can put it back and nothing the person wrote is ever lost.
-- NULL for answers written from scratch by the AI.

ALTER TABLE application_answers ADD COLUMN source_draft TEXT;
