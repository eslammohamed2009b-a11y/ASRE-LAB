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
- [ ] Reconcile final validation evidence with README and scientific capability documentation.

## Current local validation evidence (2026-07-22)

- Focused pipeline/field/analysis/routes: **16 passed**.
- Unit: **38 passed**.
- Integration: **38 passed**.
- Benchmark: **8 passed**.
- E2E: **5 passed**.
- Combined local selection: **89 passed, 13 deselected**.
- External: **3 skipped** because live Supabase credentials are unavailable; these are blocked,
  not passing evidence.
- Windows-native caveat: after pytest prints successful completion, the shared CadQuery/OCP
  interpreter terminates with Windows status `0xC0000005` during process teardown. The same
  post-summary status occurs for every marker split, including external collection, and is not
  associated with a failed assertion. It remains an environment/runtime issue to resolve or
  reproduce in CI; the production release gate therefore remains NO-GO.

## Test commands

From `backend/` in the pinned Python 3.11 environment:

```text
python -m pytest tests/unit/test_pipeline_persistence.py \
  tests/integration/test_solver_field_integration.py \
  tests/integration/test_analysis_api.py -q
python -m pytest -m "unit or integration or benchmark or e2e" -q
python -m pytest -m external -q
```

External tests that skip for missing credentials are **blocked/skipped**, never passing evidence.
