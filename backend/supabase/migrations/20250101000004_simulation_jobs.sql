-- ASRE-LAB migration 004: simulation_jobs (Module 2 async simulation job tracking)
-- Idempotent. Depends on 001_initial_schema.sql (profiles, experiments, design_models).
--
-- Mirrors the ownership/status/idempotency model already used by
-- `generation_jobs` (see 003_job_tracking.sql) so Module 2 reuses the same
-- job architecture instead of a separate, incompatible one.

create table if not exists public.simulation_jobs (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid references public.experiments(id) on delete cascade,
  design_id uuid references public.design_models(id) on delete set null,
  user_id uuid not null references public.profiles(id) on delete cascade,
  solver_id text not null,
  status text not null default 'queued',
  progress_percent integer not null default 0,
  idempotency_key text,
  error_code text,
  safe_error_message text,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz,
  updated_at timestamptz not null default now(),
  constraint simulation_jobs_status_check check (
    status in ('queued', 'running', 'completed', 'failed', 'cancelled')
  ),
  constraint simulation_jobs_progress_check check (progress_percent between 0 and 100)
);

create index if not exists idx_simulation_jobs_user_id on public.simulation_jobs(user_id);
create index if not exists idx_simulation_jobs_experiment_id on public.simulation_jobs(experiment_id);
create index if not exists idx_simulation_jobs_design_id on public.simulation_jobs(design_id);
create index if not exists idx_simulation_jobs_status on public.simulation_jobs(status);
create index if not exists idx_simulation_jobs_solver_id on public.simulation_jobs(solver_id);

create unique index if not exists simulation_jobs_user_idempotency_key
  on public.simulation_jobs(user_id, idempotency_key)
  where idempotency_key is not null;

drop trigger if exists trg_simulation_jobs_updated_at on public.simulation_jobs;
create trigger trg_simulation_jobs_updated_at
before update on public.simulation_jobs
for each row
execute function public.set_updated_at();

alter table public.simulation_jobs enable row level security;

drop policy if exists simulation_jobs_owner_all on public.simulation_jobs;
create policy simulation_jobs_owner_all on public.simulation_jobs
for all
using (auth.uid() = user_id)
with check (auth.uid() = user_id);
