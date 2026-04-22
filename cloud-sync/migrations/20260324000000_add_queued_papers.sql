-- Add queued_papers JSONB column to snapshots for daily email suggestions
ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS queued_papers JSONB DEFAULT '[]'::jsonb;
