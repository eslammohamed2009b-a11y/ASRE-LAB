-- ASRE-LAB migration 005: simulation_inputs (Module 2 immutable request snapshot)
-- Idempotent. Depends on 004_simulation_jobs.sql.
--
-- One row per simulation_jobs row (1:1), insert-only: the exact material,
-- units, initial/boundary conditions, and numerical settings a job was
-- requested with. Never updated after insert, so it stays trustworthy
-- evidence of what a persisted result was actually computed from.

create table if not exists public.simulation_inputs (
  simulation_id uuid primary key references public.simulation_jobs(id) on delete cascade,
  material_name text not null,
  material_properties jsonb not null default '{}'::jsonb,
  units jsonb not null default '{}'::jsonb,
  initial_conditions jsonb not null default '{}'::jsonb,
  boundary_conditions jsonb not null default '{}'::jsonb,
  numerical_settings jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

alter table public.simulation_inputs enable row level security;

drop policy if exists simulation_inputs_owner_all on public.simulation_inputs;
create policy simulation_inputs_owner_all on public.simulation_inputs
for all
using (
  exists (
    select 1 from public.simulation_jobs j
    where j.id = simulation_inputs.simulation_id and j.user_id = auth.uid()
  )
)
with check (
  exists (
    select 1 from public.simulation_jobs j
    where j.id = simulation_inputs.simulation_id and j.user_id = auth.uid()
  )
);
