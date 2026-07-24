-- ASRE-LAB migration 009: persisted, owner-scoped Module 3 engineering analyses.
-- Deterministic datasets and evidence-grounded outputs are retained as bounded JSON;
-- raw multidimensional solver arrays remain in private field-result artifacts.

alter table public.simulation_results
  add column if not exists status text not null default 'completed',
  add column if not exists numerical_method text not null default '',
  add column if not exists residual_history jsonb not null default '[]'::jsonb,
  add column if not exists validation_metadata jsonb not null default '{}'::jsonb,
  add column if not exists elapsed_time_seconds double precision,
  add column if not exists reproducibility_hash text not null default '',
  add column if not exists source_design_id uuid references public.design_models(id) on delete set null;



create table if not exists public.experiment_analyses (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.experiments(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  analysis_type text not null check (char_length(analysis_type) between 1 and 80),
  status text not null check (status in ('queued', 'running', 'completed', 'failed', 'cancelled')),
  dataset_hash text not null check (dataset_hash ~ '^[0-9a-f]{64}$'),
  configuration jsonb not null default '{}'::jsonb,
  result jsonb not null default '{}'::jsonb,
  warnings jsonb not null default '[]'::jsonb,
  source_design_ids jsonb not null default '[]'::jsonb,
  source_simulation_ids jsonb not null default '[]'::jsonb,
  data_quality jsonb not null default '{}'::jsonb,
  engine_version text not null check (char_length(engine_version) between 1 and 40),
  reproducibility_hash text not null check (reproducibility_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_experiment_analyses_experiment
  on public.experiment_analyses(experiment_id, created_at, id);
create index if not exists idx_experiment_analyses_user
  on public.experiment_analyses(user_id, created_at);

alter table public.experiment_analyses enable row level security;
drop policy if exists experiment_analyses_owner_all on public.experiment_analyses;
create policy experiment_analyses_owner_all on public.experiment_analyses
for all using (user_id = auth.uid()) with check (user_id = auth.uid());
