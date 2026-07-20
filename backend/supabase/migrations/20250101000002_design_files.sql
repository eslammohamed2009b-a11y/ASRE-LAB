-- ASRE-LAB migration 002: design_files (durable ownership + storage location)
-- Idempotent. Depends on 001_initial_schema.sql (experiments, design_models, profiles).

create table if not exists public.design_files (
  id uuid primary key default gen_random_uuid(),
  design_model_id uuid references public.design_models(id) on delete cascade,
  experiment_id uuid references public.experiments(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  file_format text not null,
  storage_provider text not null default 'local',
  object_key text not null,
  file_size_bytes bigint,
  checksum_sha256 text,
  media_type text not null default 'application/octet-stream',
  created_at timestamptz not null default now()
);

create index if not exists idx_design_files_user_id on public.design_files(user_id);
create index if not exists idx_design_files_experiment_id on public.design_files(experiment_id);
create index if not exists idx_design_files_design_model_id on public.design_files(design_model_id);

-- object_key is never client-supplied and always namespaced per-user
-- (users/{user_id}/experiments/{experiment_id}/designs/{design_id}/{filename}
-- - see app/core/storage.py FileStorage.validate_object_key), so a unique
-- constraint here also guards against accidental key collisions.
alter table public.design_files drop constraint if exists design_files_object_key_key;
alter table public.design_files add constraint design_files_object_key_key unique (object_key);

alter table public.design_files enable row level security;

drop policy if exists design_files_owner_all on public.design_files;
create policy design_files_owner_all on public.design_files
for all
using (auth.uid() = user_id)
with check (auth.uid() = user_id);
