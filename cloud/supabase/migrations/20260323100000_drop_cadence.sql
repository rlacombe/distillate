-- Remove cadence column: email types are independent boolean toggles
-- (daily_papers, weekly_digest, experiment_reports), not a single cadence.

-- Drop the cadence-based index
DROP INDEX IF EXISTS idx_users_cadence;

-- Drop cadence column
ALTER TABLE users DROP COLUMN IF EXISTS cadence;

-- Fix preferred_hour default (6am, not 7)
ALTER TABLE users ALTER COLUMN preferred_hour SET DEFAULT 6;

-- Add daily_papers/weekly_digest columns if missing (may already exist
-- from sync-snapshot upserts, but schema should declare them)
DO $$ BEGIN
  ALTER TABLE users ADD COLUMN daily_papers BOOLEAN NOT NULL DEFAULT true;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

DO $$ BEGIN
  ALTER TABLE users ADD COLUMN weekly_digest BOOLEAN NOT NULL DEFAULT true;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

-- New index on the boolean toggles
CREATE INDEX IF NOT EXISTS idx_users_email_enabled
  ON users (preferred_hour)
  WHERE daily_papers OR weekly_digest;

-- Rewrite get_users_due_for_email: check individual toggles, not cadence
CREATE OR REPLACE FUNCTION get_users_due_for_email()
RETURNS SETOF users AS $$
BEGIN
  RETURN QUERY
  SELECT u.*
  FROM users u
  WHERE (u.daily_papers OR u.weekly_digest)
    -- It's their preferred hour in their timezone
    AND EXTRACT(HOUR FROM NOW() AT TIME ZONE u.timezone) = u.preferred_hour
    -- Haven't been emailed in the last 20 hours (dedup guard)
    AND (u.last_email_at IS NULL OR u.last_email_at < NOW() - INTERVAL '20 hours');
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
