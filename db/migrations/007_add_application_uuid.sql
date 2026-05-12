-- Migration 007: Add UUID column for URL-safe application routing
--
-- Background: application IDs are human-readable slugs derived from
-- company + role names (e.g. "2026-05-12_areti-group_implementation-/-onboarding-specialist").
-- Special characters in role titles — particularly '/' — break URL routing
-- because Next.js and FastAPI both treat them as path separators.
--
-- Fix: add a uuid column used exclusively for URL routing. The id column
-- (slug) remains the primary key and is still used by all FK relationships
-- and filesystem paths. uuid is only a lookup key for incoming API requests.
--
-- Existing rows are backfilled with a UUID v4-format string generated via
-- SQLite's randomblob(). New rows receive a uuid generated in Python at
-- insert time.
--
-- After this migration, all API routes that previously accepted the slug as
-- a path parameter now accept uuid instead.

ALTER TABLE applications ADD COLUMN uuid TEXT DEFAULT '';

-- Backfill existing rows with a UUID v4-format identifier.
-- randomblob(n) returns n random bytes; hex() converts to uppercase hex;
-- lower() normalises to lowercase. The result is an 8-4-4-4-12 format string.
UPDATE applications
SET uuid = (
    lower(hex(randomblob(4))) || '-' ||
    lower(hex(randomblob(2))) || '-' ||
    lower(hex(randomblob(2))) || '-' ||
    lower(hex(randomblob(2))) || '-' ||
    lower(hex(randomblob(6)))
)
WHERE uuid IS NULL OR uuid = '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_applications_uuid ON applications(uuid);
