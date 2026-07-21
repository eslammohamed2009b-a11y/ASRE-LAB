-- ASRE-LAB migration 008: bounded scientific field artifact metadata.
-- Full numerical arrays are stored privately through FileStorage; this table
-- contains owner-scoped metadata and integrity evidence only.

create table if not exists public.simulation_field_results (
  id uuid primary key default gen_random_uuid(),
  simulation_id uuid not null references public.simulation_jobs(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  variable_name text not null check (char_length(variable_name) between 1 and 80),
  unit text not null check (char_length(unit) between 1 and 64),
  format text not null check (format = 'numpy_npz'),
  format_version text not null,
  dimensions integer not null check (dimensions between 1 and 4),
  axes jsonb not null default '[]'::jsonb,
  array_shape jsonb not null default '[]'::jsonb,
  grid_metadata jsonb not null default '{}'::jsonb,
  storage_object_key text not null unique,
  checksum_sha256 text not null check (checksum_sha256 ~ '^[0-9a-f]{64}$'),
  byte_size bigint not null check (byte_size between 1 and 33554432),
  minimum double precision not null,
  maximum double precision not null,
  mean double precision not null,
  preview jsonb not null default '[]'::jsonb,
  reproducibility_hash text not null check (reproducibility_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now()
);

create index if not exists idx_field_results_simulation on public.simulation_field_results(simulation_id);
create index if not exists idx_field_results_user on public.simulation_field_results(user_id);

alter table public.simulation_field_results enable row level security;
drop policy if exists simulation_field_results_owner_all on public.simulation_field_results;
create policy simulation_field_results_owner_all on public.simulation_field_results
for all using (user_id = auth.uid()) with check (user_id = auth.uid());
