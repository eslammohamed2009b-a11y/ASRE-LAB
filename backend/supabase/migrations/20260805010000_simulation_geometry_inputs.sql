-- Persist the exact solver geometry snapshot alongside every immutable input.
alter table public.simulation_inputs
  add column if not exists geometry jsonb not null default '{}'::jsonb;
