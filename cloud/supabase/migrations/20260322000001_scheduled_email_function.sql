-- Function to find users who are due for a scheduled email right now.
-- Called by the hourly cron trigger.
-- Returns users whose local time matches preferred_hour and who have
-- at least one email type (daily_papers or weekly_digest) enabled.

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
