# ASRE-LAB Backend Go/No-Go Checklist

Current consolidated status for the Big Batch 2 draft branch. Historical snapshots were
removed because they described superseded code and produced contradictory capability claims.

## Release gate

The backend is **NO-GO for production merge/deployment** until every external-infrastructure
item below is validated. Local implementation evidence does not substitute for live Supabase,
Redis/Celery, CI, or production validation.

## Implemented and locally testable

- [x] FastAPI authentication and owner-scoped API behavior.
- [x] Parametric CadQuery generation with STEP/STL storage contracts.
- [x] Durable experiments, designs, jobs, simulation inputs/results, field metadata, and analyses.
- [x] SQLite and Supabase repository adapters use the shared `PersistenceRepository` contract.
- [x] Real bounded thermal, structural, and modal methods documented in
  `docs/SCIENTIFIC_CAPABILITY_GAPS.md`.
- [x] Genuine solver fields use bounded NPZ artifacts, integrity checks, safe keys, and
  owner-scoped retrieval.
- [x] Deterministic Module 3 dataset construction, descriptive statistics, association,
  first-order standardized regression, Pareto analysis, ranking, and evidence-linked advice.
- [x] The authoritative `/api/pipeline` forward path persists designs, executes unified real
  thermal/structural reference scenarios and field artifacts, then persists Module 3 analysis.
- [x] Unsupported wind/CFD requests fail without an empirical or fabricated fallback.
- [x] Legacy `/api/simulate/*` is deprecated and isolated from the authoritative pipeline.

## Scientific scope gates

- [x] Correlation is labelled association and never causation.
- [x] Standardized regression is labelled a first-order linear sensitivity estimate, not Sobol,
  global, or causal sensitivity.
- [x] SDOF modal analysis remains scalar-only.
- [x] Pipeline thermal/structural inputs are disclosed comparison scenarios, not inferred service
  conditions or arbitrary-CAD mesh simulations.
- [ ] CFD flow-field solver: not implemented.
- [ ] Acoustic/wave solver: not implemented.
- [ ] Electromagnetic solver: not implemented.
- [ ] Coupled multiphysics: not implemented.
- [ ] Automated Module 3 → Module 1 reviewed design iteration: not implemented.

## Validation required before merge or deployment

- [ ] Review all Draft PR changes and obtain explicit merge approval.
- [ ] Apply Migration 009 to a disposable/staging Supabase project.
- [ ] Run live Supabase repository, storage, RLS, field-result, and analysis round trips.
- [ ] Validate Redis with separate Celery workers, including restart, retry, cancellation,
  concurrency, partial failure, and load behavior.
- [ ] Obtain a successful remote CI run of the required backend suites.
- [ ] Run final production-like Module 1 → Module 2 → Module 3 end-to-end validation.
- [x] Reconcile local validation evidence with README and scientific capability documentation.

## Current local validation evidence (2026-07-22)

- Combined unit/integration/E2E/benchmark selection: **92 passed, 14 deselected**; real process
  exit code 0.
- Complete backend suite: **102 passed, 4 skipped**; real process exit code 0.
- The four external Supabase tests skipped because live credentials are unavailable; these are
  blocked evidence, never passing evidence.
- The Windows shutdown crash was reproduced as a native interaction between the CadQuery 2.4
  dependency set's NLopt and CasADi imports. The pinned CadQuery 2.8/OCP 7.9 dependency set plus
  the Windows DLL bootstrap exits cleanly after STEP/STL generation. A subprocess regression test
  now requires a genuine zero process exit code, so a post-summary native crash cannot be mistaken
  for passing evidence.

## Test commands

From `backend/` in the pinned Python 3.11 environment:

```text
python -m pytest tests/unit/test_pipeline_persistence.py \
  tests/integration/test_solver_field_integration.py \
  tests/integration/test_analysis_api.py -q
python -m pytest -m "unit or integration or benchmark or e2e" -q
python -m pytest -m external -q
./scripts/validate_supabase_release_gate.ps1
```

External tests that skip for missing credentials are **blocked/skipped**, never passing evidence.
