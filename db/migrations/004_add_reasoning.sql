-- Migration 004: Add reasoning column to applications
-- Stores the LLM chain-of-thought reasoning captured at generation time.

ALTER TABLE applications ADD COLUMN reasoning TEXT;
