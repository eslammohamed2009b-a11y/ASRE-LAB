-- ASRE-LAB migration 012: API-role grants and private artifact storage.
--
-- RLS policies do not grant table privileges by themselves. Grant the
-- Supabase API roles access to the application schema so authenticated
-- requests can reach RLS and the service role can perform backend work.
grant usage on schema public to authenticated, service_role;
grant all privileges on all tables in schema public to authenticated, service_role;
grant all privileges on all sequences in schema public to authenticated, service_role;
grant execute on all functions in schema public to authenticated, service_role;

alter default privileges in schema public
  grant all privileges on tables to authenticated, service_role;
alter default privileges in schema public
  grant all privileges on sequences to authenticated, service_role;
alter default privileges in schema public
  grant execute on functions to authenticated, service_role;

-- Backend artifacts are private. Access is mediated by the backend service
-- role; no storage.objects policy grants direct anon/authenticated access.
insert into storage.buckets (id, name, public)
values ('design-files', 'design-files', false)
on conflict (id) do update
set public = excluded.public;
