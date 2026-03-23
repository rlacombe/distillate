-- Function to find users who are due for a scheduled email right now.
-- Called by the hourly cron trigger.
-- Matches users whose local time == preferred_hour and respects cadence.

CREATE OR REPLACE FUNCTION get_users_due_for_email()
RETURNS SETOF users AS $$
BEGIN
  RETURN QUERY
  SELECT u.*
  FROM users u
  WHERE u.cadence != 'off'
    -- It's their preferred hour in their timezone
    AND EXTRACT(HOUR FROM NOW() AT TIME ZONE u.timezone) = u.preferred_hour
    -- For weekly users, it's their chosen day
    AND (
      u.cadence = 'daily'
      OR (u.cadence = 'weekly' AND EXTRACT(DOW FROM NOW() AT TIME ZONE u.timezone) = u.digest_day)
    )
    -- Haven't been emailed in the last 20 hours (dedup guard)
    AND (u.last_email_at IS NULL OR u.last_email_at < NOW() - INTERVAL '20 hours');
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Cron schedule: run every hour at minute 0
-- Enable pg_cron extension first via Supabase dashboard, then run:
--
-- SELECT cron.schedule(
--   'distillate-scheduled-emails',
--   '0 * * * *',
--   $$SELECT net.http_post(
--     url := 'https://YOUR_PROJECT.supabase.co/functions/v1/send-scheduled',
--     headers := '{"Authorization": "Bearer YOUR_SERVICE_ROLE_KEY"}'::jsonb,
--     body := '{}'::jsonb
--   );$$
-- );
