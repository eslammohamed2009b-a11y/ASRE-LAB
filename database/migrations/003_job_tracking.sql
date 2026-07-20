-- ASRE-LAB migration 003: generation_jobs (async batch generation tracking)
-- Idempotent. Depends on 001_initial_schema.sql (experiments, profiles).

create table if not exists public.generation_jobs (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.experiments(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  job_type text not null default 'design_batch',
  status text not null default 'queued',
  requested_count integer not null,
  completed_count integer not null default 0,
  failed_count integer not null default 0,
  progress_percent integer not null default 0,
  error_code text,
  safe_error_message text,
  idempotency_key text,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz,
  updated_at timestamptz not null default now(),
  constraint generation_jobs_status_check check (
    status in ('queued', 'running', 'completed', 'partial_failure', 'failed', 'cancelled')
  ),
  constraint generation_jobs_progress_check check (progress_percent between 0 and 100)
);

create index if not exists idx_generation_jobs_user_id on public.generation_jobs(user_id);
create index if not exists idx_generation_jobs_experiment_id on public.generation_jobs(experiment_id);
create index if not exists idx_generation_jobs_status on public.generation_jobs(status);

-- Duplicate/idempotent request protection: at most one job per
-- (user_id, idempotency_key) when a client supplies one.
alter table public.generation_jobs drop constraint if exists generation_jobs_user_idempotency_key;
create unique index if not exists generation_jobs_user_idempotency_key
  on public.generation_jobs(user_id, idempotency_key)
  where idempotency_key is not null;

drop trigger if exists trg_generation_jobs_updated_at on public.generation_jobs;
create trigger trg_generation_jobs_updated_at
before update on public.generation_jobs
for each row
execute function public.set_updated_at();

alter table public.generation_jobs enable row level security;

drop policy if exists generation_jobs_owner_all on public.generation_jobs;
create policy generation_jobs_owner_all on public.generation_jobs
for all
using (auth.uid() = user_id)
with check (auth.uid() = user_id);
