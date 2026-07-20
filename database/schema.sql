-- DEPRECATED - DO NOT APPLY THIS FILE.
--
-- This file and `database/supabase_schema.sql` were two divergent,
-- contradictory full schemas (different table names/columns for the same
-- concepts - e.g. this file used `owner_id`/`storage_path`, the other used
-- `user_id` with a different table set entirely). They have been
-- consolidated into a single authoritative, ordered, idempotent migrations
-- directory: see `database/migrations/` (start with
-- `database/migrations/README.md`).
--
-- This file is kept only for historical reference (git blame) and MUST
-- NOT be applied to any database. Use database/migrations/*.sql instead.

-- ASRE-LAB Supabase schema
-- Core entities requested for bootstrap: profiles, experiments, design_models, simulation_metrics
--
-- Migration note (durable persistence/ownership hardening): `design_models`
-- already stores one row per generated variation, keyed by
-- (experiment_id, variation_index) — a separate `design_variants` table
-- would duplicate that key and was judged unnecessary. What was missing
-- was a table to track *ownership + storage location* of exported design
-- files (STL/STEP) independent of whether a `design_models` row exists
-- for that generation (e.g. single ad-hoc `/api/design/generate-single`
-- calls, which do not populate `design_models`). `design_files` below
-- fills that gap and is the backing store for
-- `app.core.repository.SupabaseRepository`.

create extension if not exists "pgcrypto";

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  full_name text,
  role text not null default 'researcher',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.experiments (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references public.profiles(id) on delete cascade,
  title text not null,
  description text,
  status text not null default 'draft',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.design_models (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.experiments(id) on delete cascade,
  variation_index integer not null,
  base numeric,
  height numeric,
  angle numeric,
  material text,
  stl_path text,
  step_path text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (experiment_id, variation_index)
);

create table if not exists public.simulation_metrics (
  id uuid primary key default gen_random_uuid(),
  design_model_id uuid not null references public.design_models(id) on delete cascade,
  analysis_type text not null,
  max_temperature numeric,
  max_stress numeric,
  avg_temperature numeric,
  drag_coefficient numeric,
  raw_metrics jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_experiments_owner_id on public.experiments(owner_id);
create index if not exists idx_design_models_experiment_id on public.design_models(experiment_id);
create index if not exists idx_simulation_metrics_design_model_id on public.simulation_metrics(design_model_id);
create index if not exists idx_simulation_metrics_analysis_type on public.simulation_metrics(analysis_type);

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

alter table public.profiles enable row level security;
alter table public.experiments enable row level security;
alter table public.design_models enable row level security;
alter table public.simulation_metrics enable row level security;

-- Baseline policies. Tighten to your tenant model before production.
drop policy if exists profiles_owner_select on public.profiles;
create policy profiles_owner_select on public.profiles for select using (auth.uid() = id);

drop policy if exists experiments_owner_all on public.experiments;
create policy experiments_owner_all on public.experiments
for all
using (auth.uid() = owner_id)
with check (auth.uid() = owner_id);

drop policy if exists design_models_owner_all on public.design_models;
create policy design_models_owner_all on public.design_models
for all
using (
  exists (
    select 1 from public.experiments e
    where e.id = design_models.experiment_id and e.owner_id = auth.uid()
  )
)
with check (
  exists (
    select 1 from public.experiments e
    where e.id = design_models.experiment_id and e.owner_id = auth.uid()
  )
);

drop policy if exists simulation_metrics_owner_all on public.simulation_metrics;
create policy simulation_metrics_owner_all on public.simulation_metrics
for all
using (
  exists (
    select 1
    from public.design_models d
    join public.experiments e on e.id = d.experiment_id
    where d.id = simulation_metrics.design_model_id
      and e.owner_id = auth.uid()
  )
)
with check (
  exists (
    select 1
    from public.design_models d
    join public.experiments e on e.id = d.experiment_id
    where d.id = simulation_metrics.design_model_id
      and e.owner_id = auth.uid()
  )
);

-- design_files: durable ownership + storage location of exported design
-- files (STL/STEP), independent of the design_models pipeline table.
-- user_id is the direct, authoritative owner for fail-closed ownership
-- checks in application code (see app.core.repository) — RLS below is
-- defense-in-depth, not the sole enforcement mechanism, because the
-- backend currently talks to Supabase through a single shared client
-- rather than a per-request, per-user authenticated session.
create table if not exists public.design_files (
  id uuid primary key default gen_random_uuid(),
  design_model_id uuid references public.design_models(id) on delete cascade,
  experiment_id uuid references public.experiments(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  file_format text not null,
  storage_path text not null,
  file_size_bytes bigint,
  checksum text,
  created_at timestamptz not null default now()
);

create index if not exists idx_design_files_user_id on public.design_files(user_id);
create index if not exists idx_design_files_experiment_id on public.design_files(experiment_id);

alter table public.design_files enable row level security;

drop policy if exists design_files_owner_all on public.design_files;
create policy design_files_owner_all on public.design_files
for all
using (auth.uid() = user_id)
with check (auth.uid() = user_id);
