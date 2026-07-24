-- Reviewable Module 3 -> Module 1 proposals and explicit iteration lineage.
create table if not exists public.design_improvement_proposals (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.experiments(id) on delete cascade,
  analysis_id uuid not null references public.experiment_analyses(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  status text not null check (status in ('generated','accepted','rejected','superseded','executed','failed')),
  modifications jsonb not null default '[]'::jsonb,
  evidence jsonb not null default '[]'::jsonb,
  source_design_ids jsonb not null default '[]'::jsonb,
  expected_tradeoffs jsonb not null default '[]'::jsonb,
  confidence_limitations jsonb not null default '[]'::jsonb,
  constraint_checks jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index if not exists idx_design_proposals_experiment on public.design_improvement_proposals(experiment_id,created_at,id);
alter table public.design_improvement_proposals enable row level security;
drop policy if exists design_proposals_owner_all on public.design_improvement_proposals;
create policy design_proposals_owner_all on public.design_improvement_proposals for all
using (user_id=auth.uid()) with check (user_id=auth.uid());

create table if not exists public.design_iterations (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.experiments(id) on delete cascade,
  proposal_id uuid not null unique references public.design_improvement_proposals(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  parent_design_ids jsonb not null default '[]'::jsonb,
  child_design_ids jsonb not null default '[]'::jsonb,
  status text not null check (status in ('planned','completed','failed')),
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index if not exists idx_design_iterations_experiment on public.design_iterations(experiment_id,created_at,id);
alter table public.design_iterations enable row level security;
drop policy if exists design_iterations_owner_all on public.design_iterations;
create policy design_iterations_owner_all on public.design_iterations for all
using (user_id=auth.uid()) with check (user_id=auth.uid());
