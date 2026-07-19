-- ASRE-LAB Supabase schema
-- Run this script in Supabase SQL Editor

create extension if not exists "pgcrypto";

create table if not exists public.users (
  id uuid primary key default gen_random_uuid(),
  email text unique not null,
  full_name text not null,
  role text not null default 'researcher',
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.experiments (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  title text not null,
  description text,
  status text not null default 'draft',
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.simulation_results (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.experiments(id) on delete cascade,
  result_version integer not null default 1,
  metrics jsonb not null default '{}'::jsonb,
  output_uri text,
  execution_time_ms integer,
  created_at timestamptz not null default now()
);

create index if not exists idx_experiments_user_id on public.experiments(user_id);
create index if not exists idx_simulation_results_experiment_id on public.simulation_results(experiment_id);
create index if not exists idx_experiments_status on public.experiments(status);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_users_updated_at on public.users;
create trigger trg_users_updated_at
before update on public.users
for each row
execute function public.set_updated_at();

drop trigger if exists trg_experiments_updated_at on public.experiments;
create trigger trg_experiments_updated_at
before update on public.experiments
for each row
execute function public.set_updated_at();

alter table public.users enable row level security;
alter table public.experiments enable row level security;
alter table public.simulation_results enable row level security;

-- Basic policies; tighten later based on your auth model
drop policy if exists users_select_all on public.users;
create policy users_select_all on public.users for select using (true);

drop policy if exists experiments_select_all on public.experiments;
create policy experiments_select_all on public.experiments for select using (true);

drop policy if exists simulation_results_select_all on public.simulation_results;
create policy simulation_results_select_all on public.simulation_results for select using (true);
