# ASRE-LAB Final Go/No-Go Checklist

This checklist defines objective acceptance gates for final project sign-off.
Status values per item:
- `GO`: Requirement is implemented, verified, and documented.
- `NO-GO`: Requirement is missing, incomplete, or unverified.

## Phase 1: Physics Core Reinforcement (Module 2 Focus)

### 1) Replace scaffold solvers with real physics solver
- Scope:
  - Replace simplified logic in `backend/app/module2_simulation/solvers/thermal_solver.py`
  - Use a real FEA/FVM stack (e.g., FEniCSx, SfePy, or equivalent production-grade solver)
  - Implement governing equation solving and numerical boundary-condition handling
- Acceptance criteria:
  - Thermal results are generated from numerical solver outputs, not fixed/synthetic values
  - Solver accepts mesh, material, and boundary conditions with validation
  - Reproducible run given same inputs (within numeric tolerance)
  - Benchmark case validated against known reference
- Evidence required:
  - Solver module commit
  - Validation notebook/report
  - API sample response with real field values
- Gate:
  - GO only if all criteria and evidence are complete

### 2) Job queue for simulation workloads
- Scope:
  - Introduce background job system (Celery + Redis recommended)
  - Move long-running design/simulation tasks off request thread
  - Add job status endpoints and failure handling
- Acceptance criteria:
  - API returns `job_id` for async runs
  - Supports 100 parallel jobs without API timeout collapse
  - Retries and terminal-failure states are visible (`queued/running/succeeded/failed`)
  - Resource limits and worker concurrency are configurable
- Evidence required:
  - Queue configuration files
  - Load test report for 100-job run
  - Job lifecycle API examples
- Gate:
  - GO only if load test passes and failure paths are observable

## Phase 2: Integration and Verification

### 3) Full persistence integration with Supabase
- Scope:
  - Persist outputs of Module 1, Module 2, and Module 3
  - Store metadata + artifacts references for reproducibility
  - Link records to experiment and owner
- Acceptance criteria:
  - Every pipeline run creates traceable records in DB
  - Can reconstruct a previous run from DB records only
  - Data model includes versioning/timestamps
  - No orphan records under normal failure conditions
- Evidence required:
  - Migration/schema files
  - End-to-end run showing persisted records
  - Re-run script proving reproducibility
- Gate:
  - GO only if reproducibility replay is successful

### 4) Automated tests for critical pipeline
- Scope:
  - Add `pytest` coverage for Module1->Module2->Module3 critical path
  - Include success/failure and edge-case scenarios
- Acceptance criteria:
  - Unit tests for each module contract
  - Integration tests for `/api/pipeline/run`
  - Regression tests for deterministic fallback paths
  - CI test job blocks merge on failure
- Evidence required:
  - `tests/` suite
  - CI logs showing passing pipeline
  - Coverage summary for critical modules
- Gate:
  - GO only if CI is green and critical scenarios pass

## Phase 3: Security and Production Hardening

### 5) API security and per-user isolation
- Scope:
  - JWT authentication via FastAPI dependencies
  - Ownership checks on experiments and outputs
  - Enforce access boundaries per user
- Acceptance criteria:
  - Unauthorized requests rejected (401/403)
  - Cross-user data access blocked
  - Auth context propagated to DB writes and reads
  - Secrets managed via env and deployment secrets
- Evidence required:
  - Auth middleware/dependencies
  - Security test cases
  - Example blocked cross-user request
- Gate:
  - GO only if isolation tests pass

### 6) Final API documentation quality
- Scope:
  - Ensure OpenAPI/Swagger reflects real contracts
  - Add concise endpoint descriptions and examples
- Acceptance criteria:
  - Every endpoint has request/response schema and examples
  - Error models documented for major failure modes
  - Pipeline and async job lifecycle documented end-to-end
- Evidence required:
  - OpenAPI spec snapshot
  - API docs review checklist
- Gate:
  - GO only if docs are complete and match implementation

## Global Final Sign-off Rule
Project is `FINAL GO` only if all six gates above are `GO`.
If any gate remains `NO-GO`, the project remains in pre-production status.

## Latest Snapshot (2026-07-19, licensing + CI validation + durable persistence session)

> This section supersedes the "Current Snapshot" below for gates 3 and 5
> only (durable persistence/ownership work done this session). Gates 1, 2,
> 6 are unchanged from the prior snapshot, kept further down for
> traceability.

- **3) Full persistence/replayability: `NO-GO` (upgraded from a hard
  blocker to code-complete-but-externally-unverified)**
  - Done: `app/core/repository.py` — a `PersistenceRepository` interface
    with a `SupabaseRepository` production adapter and a
    `LocalSQLiteRepository` deterministic adapter backed by a real on-disk
    SQLite file. New `database/schema.sql` migration adds a `design_files`
    table (id, design_model_id, experiment_id, user_id, file_format,
    storage_path, file_size_bytes, checksum, created_at) with RLS.
    Restart-durability and multi-instance state sharing are proven with
    real unit tests (a fresh repository object reads data written by a
    prior, discarded one, over the same file).
  - Missing: no live Supabase project is connected in this environment
    (confirmed via repeated secret scans — no `.env`, only placeholders in
    `.env.example`). `tests/external/test_supabase_repository_live.py`
    exists and is correctly marked `@pytest.mark.external` +
    `skipif(no credentials)` — it is **skipped, not passing** in this
    session. This remains `NO-GO` until it is actually executed against a
    real project.
- **5) JWT auth and ownership isolation: `NO-GO` (materially strengthened)**
  - Done: the in-process `ownership_store.py` (disclosed non-durable dict)
    was **removed** and replaced by the durable repository above.
    `export_stl` now validates `design_id` as a real UUID before any
    lookup (malformed ids fail closed with 404, same as unknown/foreign
    ids — no format oracle), recomputes the served path from the
    validated id and checks it resolves inside the known export
    directory (path-traversal defense), and sets an explicit
    `model/stl` media type. New integration tests cover: user A can
    download their own file, user B cannot (404), unknown id (404),
    malformed/non-uuid id (404), a path-traversal-shaped id (404), and
    ownership surviving a fresh repository object (restart simulation).
  - Missing: still no direct enumeration/brute-force-rate-limit test; RLS
    policies exist in `design_files`/`experiments` migrations but are
    defense-in-depth only (the backend uses a single shared Supabase
    client without per-request auth binding, documented explicitly in
    `app/core/repository.py`) — the real enforcement is the application-
    layer ownership check, which is what is tested.
- **Remote CI status**: `.github/workflows/deploy.yml`'s `test-backend`
  job was updated to also run `pytest -m benchmark` and `pytest -m e2e`
  (previously only `unit`+`integration`), so it now exercises the same
  31-test suite validated locally. `gh auth login` was completed this
  session (device flow), and the workflow was triggered via
  `gh workflow run` — the resulting run
  (https://github.com/eslammohamed2009b-a11y/ASRE-LAB/actions/runs/29708262236)
  ended with `status: completed`, `conclusion: startup_failure`, and zero
  jobs were ever scheduled (`.../jobs` returns `total_count: 0`).
  `repos/.../actions/permissions` confirms Actions are enabled
  (`allowed_actions: all`) for the repository, so this is not a
  disabled-Actions or workflow-syntax problem — it is consistent with a
  GitHub-side hold on hosted-runner provisioning for a brand-new account
  (a documented GitHub behavior). **This is reported as `BLOCKED`, not
  passing** — no CI run has been observed to actually execute the test
  suite remotely yet.
- **Licensing**: `GO`. Root `LICENSE` (proprietary, source-available, all
  rights reserved), `README.md` proprietary notice + Repository Status
  section, `frontend/package.json` set to `"license": "UNLICENSED"`.
- **Local test suite**: **31 passed**, 0 failed (7 unit, 18 integration, 4
  e2e, 2 benchmark), plus 1 `external` test correctly skipped/BLOCKED —
  re-run fresh in this session from `backend/.venv311` (Python 3.11.15,
  real CadQuery 2.4.0/OCP 7.7.2, no stub).

## Current Snapshot (2026-07-19, updated after REAL CadQuery kernel validation)

> The evidence below supersedes the prior "Local Test Execution Evidence"
> section (Python 3.14 + CadQuery stub), which is **no longer valid proof**
> per the explicit instruction that stub-based passes must never count as
> CAD evidence. That evidence is preserved further down for historical
> traceability only, clearly marked as superseded.

- **1) Real physics solver: `NO-GO`**
  - Done: `thermal_solver.py` rewritten as a real numerical steady-state 3D
    heat-equation solver (Gauss-Seidel finite-difference, material-based
    conductivity lookup, hotspot detection).
  - Missing: `structural_solver.py` and `cfd_solver.py` are still
    simplified/placeholder. No benchmark-vs-reference validation report
    exists for any solver yet. Not addressed in this session (Module 1
    was the explicit scope).
- **2) Job queue/async orchestration: `NO-GO`**
  - Done: Celery app + Redis broker config, async submit/status endpoints.
  - Missing: no live Redis/Celery worker run, no 100-parallel-job load
    test. Not addressed in this session.
- **3) Full persistence/replayability: `NO-GO`**
  - Done: `PersistenceService` wired into `pipeline_service.py`.
  - Missing: no live Supabase project connected in this session to prove
    end-to-end write + replay. Not addressed in this session.
- **4) Automated critical-path testing: `GO` (local, real-kernel) / `NO-GO` (remote CI unverified)**
  - Done: full suite restructured into `unit` / `integration` / `e2e`
    pytest markers (`backend/pytest.ini`). **Executed locally against the
    real CadQuery 2.4.0 + OCP native kernel on Python 3.11.15** — see
    "Real CadQuery Kernel Validation Evidence" below. Result: **14 passed**
    (2 unit — stubbed, business-logic only; 8 integration — real geometry,
    real STEP/STL export, re-import + bounding-box verification, parameter
    sensitivity; 4 e2e — real live `uvicorn` process driven over real HTTP
    with a real JWT).
  - Still missing: no cross-user ownership isolation test, no
    simulation-solver validation tests, no persistence/replay integration
    tests. `.github/workflows/deploy.yml` was updated to use the same
    Python 3.11.15 pin and to run `unit`+`integration` marker sets, but
    **no remote GitHub Actions run has been observed in this session** —
    that remains the authoritative CI-parity check.
- **5) JWT auth and ownership isolation: `NO-GO`**
  - Done: JWT dependency enforced on all routers; 401-without-token test
    passes (now in `tests/integration/`, real app, real kernel).
  - Missing: no cross-user ownership isolation test (user A vs user B),
    no direct-ID-enumeration test. Not addressed in this session.
- **6) Final API docs completeness: `NO-GO`**
  - Done: summaries/descriptions on routers; e2e test confirms
    `/openapi.json` is served and includes `/api/design/generate-single`.
  - Missing: no exported OpenAPI snapshot file, no documented error
    models, no docs review checklist artifact.

Overall status: `NO-GO` for final production sign-off. Gate 4 now has real,
real-CAD-kernel executed evidence (a first for this project). Gates 1, 2, 3,
5, 6 remain code-complete-but-unverified or partially implemented and were
outside this session's explicit scope (Module 1 environment + CAD proof).

## Real CadQuery Kernel Validation Evidence (this session — supersedes all prior CAD evidence)

- **Environment**: Python 3.11.15 installed via `uv python install 3.11`
  (standalone build, no admin rights). Clean venv created with
  `uv venv --python 3.11 .venv311` in `backend/`. Exact pins from
  `requirements.txt` installed with `uv pip install -r requirements.txt
  --python .venv311\Scripts\python.exe` — all 87 packages installed at
  their exact pinned versions, **zero version changes**, including
  `cadquery==2.4.0` and `cadquery-ocp==7.7.2`.
- **Local-only DLL workaround**: on this Windows machine, `C:\Windows\System32`
  is missing `vcruntime140.dll` / `vcruntime140_1.dll` / `msvcp140.dll`
  system-wide, which broke `nlopt`'s native extension (a transitive
  CadQuery dependency). Fixed by copying already-present, legitimate
  copies of those DLLs (from the uv-managed Python 3.11 install and from
  numpy/pandas' own vendored copies) into `.venv311/Lib/site-packages/nlopt/`
  — a local, reversible, non-admin fix. **This is a Windows-local-dev-only
  workaround**: it is not committed to git (`.venv311/` is gitignored), and
  it is not required on Linux (GitHub Actions `ubuntu-latest` runners and
  Render's Linux buildpack do not exhibit this issue — manylinux wheels
  bundle or dynamically link their own runtime).
- **Kernel import proof**: `python -c "import cadquery as cq; ..."` →
  `cadquery version: 2.4.0` / `bbox 1.0 1.0 1.0` for a real `Workplane().box(1,1,1)`.
- **Test evidence** (`backend/pytest.ini` markers `unit`/`integration`/`e2e`):
  - `pytest -m unit -q` → `2 passed` (stubbed `cadquery`, business-logic only;
    NOT proof of real CAD).
  - `pytest -m integration -q` → `8 passed` (real pyramid/tower/bridge
    generation, real STEP+STL export with non-trivial file sizes, STEP
    re-import via `cq.importers.importStep` with bounding-box assertions,
    and a parameter-sensitivity test proving different `height_m` inputs
    produce measurably different geometry/output bytes).
  - `pytest -m e2e -q` → `4 passed` (real `uvicorn app.main:app` subprocess,
    real HTTP via `httpx` — not `TestClient` — against `/health`,
    `/openapi.json`, and `/api/design/generate-single` with a real minted
    JWT; also confirms 401 without a token).
  - `pytest -q` (all categories together) → **14 passed**, no interference
    between the unit stub and the integration/e2e real-kernel tests.
- **Reproducibility artifacts added**: `backend/requirements.lock.txt`
  (full resolved dependency set via `uv pip freeze`), `backend/.python-version`
  (`3.11.15`), `backend/render.yaml` now sets `PYTHON_VERSION=3.11.15`,
  `.github/workflows/deploy.yml` now pins `python-version: '3.11.15'` and
  runs `pytest -m unit` then `pytest -m integration` as separate CI steps.
- **Not yet done**: no remote GitHub Actions run has been triggered/observed
  with these changes; e2e tests are not yet included in the CI workflow.

## [SUPERSEDED] Prior Local Test Execution Evidence (Python 3.14 + CadQuery stub)

> Kept only for historical traceability. Per explicit instruction, a passing
> test using the CadQuery stub must never be counted as proof of real CAD
> functionality — the section above is the authoritative evidence.

- Command: `python -m pytest -q` run from `backend/` using the local
  Python 3.14 interpreter.
- Result: `4 passed, 1 warning in 0.13s`.
- This run used unpinned dependency versions and a stubbed `cadquery`
  module — it proved routing/auth/pipeline logic only, never the real CAD
  kernel.
