-- Focused V2 immutable evidence envelope. Detailed domain validation remains in application code.
create table if not exists public.engineering_evidence_records (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  experiment_id uuid references public.experiments(id) on delete cascade,
  simulation_id uuid references public.simulation_jobs(id) on delete set null,
  record_type text not null check (record_type in ('scientific_trust','run_manifest','engineering_decision','job_attempt','reasoning_event','research_report')),
  status text not null, schema_version text not null default '2.0',
  payload jsonb not null, payload_checksum text not null,
  parent_record_id uuid references public.engineering_evidence_records(id) on delete set null,
  created_at timestamptz not null default now(),
  unique(user_id,record_type,payload_checksum)
);
create index if not exists idx_engineering_evidence_owner on public.engineering_evidence_records(user_id,record_type,created_at,id);
create index if not exists idx_engineering_evidence_experiment on public.engineering_evidence_records(experiment_id,record_type,created_at,id);
alter table public.engineering_evidence_records enable row level security;
drop policy if exists engineering_evidence_records_owner_all on public.engineering_evidence_records;
create policy engineering_evidence_records_owner_all on public.engineering_evidence_records for all
using (user_id=auth.uid()) with check (user_id=auth.uid());
