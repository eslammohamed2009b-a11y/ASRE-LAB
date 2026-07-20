-- ASRE-LAB migration 006: simulation_results (Module 2 unified persisted result contract)
-- Idempotent. Depends on 004_simulation_jobs.sql.
--
-- One row per simulation_jobs row (1:1), insert-only: the full evidence
-- trail for a completed run - governing equations, assumptions, warnings,
-- convergence/residual/iteration count, metrics, field values, and any
-- result-file object keys. Never updated after insert (append a new
-- simulation_jobs row + result row for a re-run instead of mutating this
-- one) so it remains immutable solver/version evidence.

create table if not exists public.simulation_results (
  simulation_id uuid primary key references public.simulation_jobs(id) on delete cascade,
  solver_id text not null,
  solver_version text not null,
  governing_equations jsonb not null default '[]'::jsonb,
  assumptions jsonb not null default '[]'::jsonb,
  warnings jsonb not null default '[]'::jsonb,
  converged boolean not null default false,
  residual double precision,
  iteration_count integer not null default 0,
  tolerance double precision,
  summary_metrics jsonb not null default '{}'::jsonb,
  field_values jsonb not null default '[]'::jsonb,
  hotspot_node_ids jsonb not null default '[]'::jsonb,
  result_object_keys jsonb not null default '[]'::jsonb,
  application_version text not null default 'unknown',
  created_at timestamptz not null default now()
);

create index if not exists idx_simulation_results_solver_id on public.simulation_results(solver_id);

alter table public.simulation_results enable row level security;

drop policy if exists simulation_results_owner_all on public.simulation_results;
create policy simulation_results_owner_all on public.simulation_results
for all
using (
  exists (
    select 1 from public.simulation_jobs j
    where j.id = simulation_results.simulation_id and j.user_id = auth.uid()
  )
)
with check (
  exists (
    select 1 from public.simulation_jobs j
    where j.id = simulation_results.simulation_id and j.user_id = auth.uid()
  )
);
