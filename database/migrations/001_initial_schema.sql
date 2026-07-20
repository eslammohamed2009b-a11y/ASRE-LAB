-- ASRE-LAB migration 001: initial authoritative schema
-- Applies to a fresh Supabase/Postgres project. Idempotent (safe to re-run).
--
-- This is the ONE authoritative source of truth for `profiles`,
-- `experiments`, and `design_models`. The old root-level `database/schema.sql`
-- and `database/supabase_schema.sql` files (two divergent full schemas) are
-- deprecated in favor of this ordered migrations/ directory - see
-- database/migrations/README.md.

create extension if not exists "pgcrypto";

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  full_name text,
  role text not null default 'researcher',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- experiments: one row per logical unit of work (a single generation or a
-- batch job groups its designs under one experiment).
create table if not exists public.experiments (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  name text not null,
  status text not null default 'draft',
  input_specification jsonb not null default '{}'::jsonb,
  application_version text not null default 'unknown',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- design_models: one row per generated geometry variant, real CadQuery
-- parameters/units captured as JSONB for full reproducibility.
create table if not exists public.design_models (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.experiments(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  geometry_family text not null,
  parameters jsonb not null default '{}'::jsonb,
  units jsonb not null default '{}'::jsonb,
  variation_index integer not null default 0,
  generation_status text not null default 'pending',
  cadquery_version text,
  application_version text not null default 'unknown',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (experiment_id, variation_index)
);

create index if not exists idx_experiments_user_id on public.experiments(user_id);
create index if not exists idx_design_models_experiment_id on public.design_models(experiment_id);
create index if not exists idx_design_models_user_id on public.design_models(user_id);
create index if not exists idx_design_models_generation_status on public.design_models(generation_status);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_profiles_updated_at on public.profiles;
create trigger trg_profiles_updated_at
before update on public.profiles
for each row
execute function public.set_updated_at();

drop trigger if exists trg_experiments_updated_at on public.experiments;
create trigger trg_experiments_updated_at
before update on public.experiments
for each row
execute function public.set_updated_at();

drop trigger if exists trg_design_models_updated_at on public.design_models;
create trigger trg_design_models_updated_at
before update on public.design_models
for each row
execute function public.set_updated_at();

alter table public.profiles enable row level security;
alter table public.experiments enable row level security;
alter table public.design_models enable row level security;

-- RLS: a user may only see/modify their own rows. This is defense-in-depth,
-- not the sole enforcement layer - the backend currently talks to Supabase
-- through a single shared, backend-only service-role-equivalent client
-- (see app/core/repository.py), so the primary enforcement is the
-- application-layer ownership check on every read. Service-role
-- credentials never reach the frontend (see .env.example /
-- app/core/config.py: SUPABASE_KEY is backend-only configuration).
drop policy if exists profiles_owner_select on public.profiles;
create policy profiles_owner_select on public.profiles for select using (auth.uid() = id);

drop policy if exists experiments_owner_all on public.experiments;
create policy experiments_owner_all on public.experiments
for all
using (auth.uid() = user_id)
with check (auth.uid() = user_id);

drop policy if exists design_models_owner_all on public.design_models;
create policy design_models_owner_all on public.design_models
for all
using (auth.uid() = user_id)
with check (auth.uid() = user_id);
