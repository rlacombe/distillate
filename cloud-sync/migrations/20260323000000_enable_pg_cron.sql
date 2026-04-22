-- Enable pg_cron and pg_net for scheduled email delivery
CREATE EXTENSION IF NOT EXISTS pg_cron WITH SCHEMA pg_catalog;
CREATE EXTENSION IF NOT EXISTS pg_net WITH SCHEMA extensions;

-- Schedule hourly trigger for send-scheduled edge function
SELECT cron.schedule(
  'distillate-scheduled-emails',
  '0 * * * *',
  $$SELECT net.http_post(
    url := 'https://eplzanzldszhyfbvlego.supabase.co/functions/v1/send-scheduled',
    headers := '{"Authorization": "Bearer ***REDACTED***"}'::jsonb,
    body := '{}'::jsonb
  );$$
);
