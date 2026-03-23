-- Distillate cloud schema: users, snapshots, events
-- Lightweight state for email notifications + future web dashboard

-- Users: email-based accounts with notification preferences
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT UNIQUE NOT NULL,
  timezone TEXT NOT NULL DEFAULT 'UTC',
  cadence TEXT NOT NULL DEFAULT 'weekly' CHECK (cadence IN ('daily', 'weekly', 'off')),
  digest_day SMALLINT NOT NULL DEFAULT 1, -- 0=Sun, 1=Mon, ..., 6=Sat
  preferred_hour SMALLINT NOT NULL DEFAULT 7 CHECK (preferred_hour BETWEEN 0 AND 23),
  experiment_reports BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_email_at TIMESTAMPTZ,
  auth_token TEXT UNIQUE NOT NULL DEFAULT encode(gen_random_bytes(32), 'hex')
);

-- Snapshots: latest state summary per user (pushed from local app)
CREATE TABLE snapshots (
  user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  papers_read INT NOT NULL DEFAULT 0,
  papers_queued INT NOT NULL DEFAULT 0,
  reading_tags TEXT[] DEFAULT '{}',
  recent_highlights TEXT[] DEFAULT '{}', -- last 5 highlight excerpts
  experiments JSONB DEFAULT '[]', -- [{name, runs, kept, best_metric, status}]
  synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Events: experiment completions, notable milestones (event-driven emails)
CREATE TABLE events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL, -- 'experiment_complete', 'goal_reached', 'new_papers'
  payload JSONB NOT NULL DEFAULT '{}',
  emailed BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_events_pending ON events (user_id, emailed) WHERE NOT emailed;
CREATE INDEX idx_users_cadence ON users (cadence) WHERE cadence != 'off';

-- Row-level security: users only see their own data
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE events ENABLE ROW LEVEL SECURITY;

-- Service role (Edge Functions) can do everything
-- Anon role can only register (insert into users)
CREATE POLICY "Users can read own data" ON users FOR SELECT USING (auth_token = current_setting('request.headers', true)::json->>'x-auth-token');
CREATE POLICY "Snapshots by auth token" ON snapshots FOR ALL USING (user_id IN (SELECT id FROM users WHERE auth_token = current_setting('request.headers', true)::json->>'x-auth-token'));
CREATE POLICY "Events by auth token" ON events FOR ALL USING (user_id IN (SELECT id FROM users WHERE auth_token = current_setting('request.headers', true)::json->>'x-auth-token'));

-- Enable pg_cron for scheduled emails
-- (Must be enabled via Supabase dashboard: Database → Extensions → pg_cron)
