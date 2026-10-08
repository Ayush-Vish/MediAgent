-- Run once in Supabase SQL Editor after the app has initialized the table.
-- The API uses a server-only PostgreSQL connection. Browser users may never
-- access this table through Supabase's public REST API, even when signed in.
alter table public.mediagent_records enable row level security;
revoke all on public.mediagent_records from anon, authenticated;

-- Add a staff role only through the privileged SQL editor / server Admin API.
-- Replace the email before running. Never trust user_metadata for authorization.
-- update auth.users
-- set raw_app_meta_data = raw_app_meta_data ||
--   '{"role":"mediagent_staff","hospital_id":"cmc-vellore"}'::jsonb
-- where email = 'YOUR_STAFF_EMAIL';
