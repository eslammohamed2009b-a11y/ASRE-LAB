-- Allow the existing immutable evidence envelope to persist the authoritative
-- typed scientific records already validated by the application layer.
alter table public.engineering_evidence_records
  drop constraint if exists engineering_evidence_records_record_type_check;

alter table public.engineering_evidence_records
  add constraint engineering_evidence_records_record_type_check check (
    record_type in (
      'scientific_trust',
      'scientific_numerical_result',
      'scientific_validity',
      'scientific_benchmark',
      'scientific_run_convergence',
      'scientific_refinement_convergence',
      'scientific_field_result',
      'scientific_analysis',
      'run_manifest',
      'engineering_decision',
      'job_attempt',
      'reasoning_event',
      'research_report'
    )
  );
