# ASRE-LAB Engineering Handoff

Factual snapshot of the repository as of commit `90156d9b2694689d10c5a9b3a4e946579d7ba9bf`
(branch `main`). This document describes **what exists today**, not a
proposed design. Where a capability is a placeholder/prototype/missing,
that is stated explicitly rather than implied to be complete.

Production backend: `https://asre-lab.onrender.com` (Render, Docker
runtime). Database: Supabase/Postgres, migrations applied.

---

## 1. Relevant repository tree

```
backend/
  app/
    main.py                      # FastAPI app, router mounting, startup checks, error handler
    pipeline_router.py           # legacy /api/pipeline/* routes (Module1->2->3 integrated flow)
    pipeline_service.py          # orchestrates the legacy integrated flow
    core/
      auth.py                    # JWT decoding + get_current_user dependency
      celery_app.py              # Celery app + broker/backend config
      config.py                  # pydantic-settings Settings (env vars)
      persistence.py             # PersistenceService (legacy, used only by pipeline_service.py)
      repository.py              # PersistenceRepository ABC + SupabaseRepository + LocalSQLiteRepository
      storage.py                 # FileStorage ABC + LocalFileStorage + SupabaseStorage
      worker_pool.py             # process pool helper for CPU-bound CAD generation
    module1_design/
      router.py                  # /api/design/* (parse, generate-single, generate-matrix, generate-batch, export)
      jobs_router.py              # /api/jobs/* (durable, ownership-checked batch job status)
      schemas.py                 # DesignParameters, batch/job request+response schemas
      nl_parser.py                # prompt -> DesignParameters
      cadquery_engine.py          # DesignParameters -> STL/STEP files (CadQuery)
      multiprocessing_generator.py# parallel design-matrix generation
      tasks.py                    # Celery tasks for async batch generation
    module2_simulation/
      router.py                  # /api/simulate/* (legacy) + /api/simulations/* (unified, Phase C8)
      schemas.py                 # legacy + unified request/result/capability schemas
      service.py                 # legacy run_simulation_service + unified create/get/cancel services
      simulation_advisor.py      # recommendation logic (legacy + registry-backed)
      solver_registry.py         # SOLVER_REGISTRY capability metadata (single source of truth)
      materials.py                # MATERIAL_LIBRARY (in-code, authoritative at request time)
      tasks.py                   # Celery tasks for async solver execution
      solvers/
        base_solver.py            # Mesh/SolverResult/BaseSolver (legacy) + EngineeringSolver (unified)
        thermal_solver.py          # ThermalSolver (legacy) + ThermalConductionSolver (unified, REAL)
        structural_solver.py       # StructuralLinearSolver (unified, REAL)
        modal_solver.py            # ModalSolver (unified, REAL)
        cfd_solver.py               # prototype-only drag-force estimator, not a field solver
    module3_analysis/
      router.py                  # /api/analyze/full-report (stateless)
      schemas.py                 # DesignResult, FullReportRequest/Response
      clustering.py               # KMeans clustering over metrics
      correlation.py              # Pearson correlation matrix + ranked relationships
      synthesis.py                 # Anthropic-backed (optional) + deterministic fallback narrative
  supabase/
    config.toml                  # Supabase CLI config (tracked, no secrets)
    migrations/                  # Supabase-CLI-format mirror of database/migrations (8 files)
  tests/
    unit/ integration/ e2e/ external/
  Dockerfile
  requirements.txt / requirements.lock.txt
  pytest.ini
database/
  migrations/                    # canonical, human-authored SQL migrations 001-007 + README.md
frontend/                        # Next.js app (out of scope for this handoff / not to be modified)
```

## 2. Exact class names and file paths

| Class | File |
|---|---|
| `DesignParameters`, `GeometryType`, `MaterialType` | [backend/app/module1_design/schemas.py](../backend/app/module1_design/schemas.py) |
| `PersistenceRepository` (ABC), `SupabaseRepository`, `LocalSQLiteRepository` | [backend/app/core/repository.py](../backend/app/core/repository.py) |
| `FileStorage` (ABC), `LocalFileStorage`, `SupabaseStorage` | [backend/app/core/storage.py](../backend/app/core/storage.py) |
| `Mesh`, `SolverResult`, `BaseSolver` (legacy), `EngineeringSolver`, `SolverValidationError` | [backend/app/module2_simulation/solvers/base_solver.py](../backend/app/module2_simulation/solvers/base_solver.py) |
| `ThermalSolver` (legacy), `ThermalConductionSolver` (unified) | [backend/app/module2_simulation/solvers/thermal_solver.py](../backend/app/module2_simulation/solvers/thermal_solver.py) |
| `StructuralLinearSolver` | [backend/app/module2_simulation/solvers/structural_solver.py](../backend/app/module2_simulation/solvers/structural_solver.py) |
| `ModalSolver` | [backend/app/module2_simulation/solvers/modal_solver.py](../backend/app/module2_simulation/solvers/modal_solver.py) |
| solver_registry `SOLVER_REGISTRY`, `CapabilityEntry`, `UnknownSolverError`, `UnsupportedCapabilityError` | [backend/app/module2_simulation/solver_registry.py](../backend/app/module2_simulation/solver_registry.py) |
| `MaterialProperty`, `MATERIAL_LIBRARY`, `MaterialNotFoundError`, `MaterialPropertyNotFoundError` | [backend/app/module2_simulation/materials.py](../backend/app/module2_simulation/materials.py) |
| `PersistenceService` (legacy, non-conforming - see §24) | [backend/app/core/persistence.py](../backend/app/core/persistence.py) |
| `Settings` | [backend/app/core/config.py](../backend/app/core/config.py) |

## 3. `BaseSolver` / `EngineeringSolver` method signatures

Legacy (`BaseSolver`, still used by `/api/simulate/*`):
```python
class BaseSolver(ABC):
    analysis_type: str = "base"
    @abstractmethod
    def solve(self, mesh: Mesh, material: str, boundary_conditions: dict) -> SolverResult: ...
```

Unified (`EngineeringSolver`, used by `/api/simulations/*`, template method in `run()`):
```python
class EngineeringSolver(ABC):
    solver_id: str = "base"

    @property
    @abstractmethod
    def capability_metadata(self) -> CapabilityEntry: ...

    @abstractmethod
    def validate_geometry(self, request: SimulationCreateRequest) -> None: ...
    @abstractmethod
    def validate_material(self, request: SimulationCreateRequest) -> dict[str, Any]: ...
    @abstractmethod
    def validate_boundary_conditions(self, request: SimulationCreateRequest) -> None: ...
    def validate_inputs(self, request: SimulationCreateRequest) -> dict[str, Any]: ...  # concrete

    @abstractmethod
    def prepare_model(self, request: SimulationCreateRequest, material_properties: dict[str, Any]) -> Any: ...
    @abstractmethod
    def generate_or_import_mesh(self, request: SimulationCreateRequest, model: Any) -> Any: ...
    @abstractmethod
    def solve(self, request: SimulationCreateRequest, model: Any, mesh: Any) -> Any: ...
    @abstractmethod
    def calculate_residual(self, raw_result: Any) -> float | None: ...
    @abstractmethod
    def check_convergence(self, raw_result: Any) -> ConvergenceStatus: ...
    @abstractmethod
    def extract_metrics(self, raw_result: Any) -> tuple[dict[str, float], list[float], list[int]]: ...

    def return_assumptions(self) -> list[str]: ...   # concrete, override to extend
    def return_warnings(self) -> list[str]: ...      # concrete, override to extend
    def serialize_results(self, raw_result: Any, convergence: ConvergenceStatus) -> SimulationResultPayload: ...  # concrete
    def run(self, request: SimulationCreateRequest) -> SimulationResultPayload: ...  # concrete, template method
```
`run()` fixed order: `validate_inputs -> prepare_model -> generate_or_import_mesh -> solve -> check_convergence -> serialize_results`.
Any `validate_*` step raises `SolverValidationError` on rejection (mapped to HTTP 422 upstream).

## 4. `SolverRegistry` contract

`SOLVER_REGISTRY: dict[str, CapabilityEntry]` in `solver_registry.py` is the **single source of truth**
for what each solver can do; the `/api/simulations/capabilities` route returns it directly (no separate
public description). Functions:
```python
def get_solver_metadata(solver_id: str) -> CapabilityEntry     # raises UnknownSolverError
def list_solvers(family: SolverFamily | None = None) -> list[CapabilityEntry]
def is_available(solver_id: str) -> bool                        # True only if implementation_status == REAL
def require_available(solver_id: str) -> CapabilityEntry        # raises UnsupportedCapabilityError otherwise
```
Currently registered real solver IDs include `thermal_conduction_v1`, `structural_linear_1d_v1`,
`modal_eigen_1d_v1`, `acoustic_duct_1d_v1`, `electrostatic_rectangular_2d_v1`, and
`cfd_laminar_channel_2d_v1`. `coupled_multiphysics_v0` remains planned because the implemented
thermal-to-structural capability is an explicitly one-way sequential workflow, not a bidirectional solver.
A separate legacy concept, `SOLVER_VALIDATION_STATUS` dict, still gates `/api/simulate/run` and only
allows `analysis_type="thermal"`.

## 5. Current solver request schemas

`SimulationCreateRequest` (unified, `/api/simulations` POST body):
```python
class SimulationCreateRequest(BaseModel):
    solver_id: str
    experiment_id: str | None = None
    design_id: str | None = None
    material: MaterialSelection            # {name: str}
    geometry: Geometry                     # dimension, length_m, cross_section_area_m2,
                                            # moment_of_inertia_m4, num_elements, grid_resolution
    boundary_conditions: BoundaryConditions = BoundaryConditions()
    initial_conditions: InitialConditions = InitialConditions()
    numerical_settings: NumericalSettings = NumericalSettings()  # max_iterations, tolerance
```
Legacy `SimulationRunRequest` (`/api/simulate/run`):
```python
class SimulationRunRequest(BaseModel):
    design_id: str = "unknown"
    geometry_type: str = "tower"
    analysis_type: AnalysisType            # thermal | structural | wind_load
    material: str = "concrete"
    boundary_conditions: dict = {}
```

## 6. Current solver result schemas

```python
class SimulationResultPayload(BaseModel):
    solver_id: str
    solver_version: str
    governing_equations: list[str]
    assumptions: list[str]
    warnings: list[str]
    convergence: ConvergenceStatus          # {converged, iterations, residual, tolerance}
    summary_metrics: dict[str, float]
    field_values: list[float]
    hotspot_node_ids: list[int]
```
Legacy `SolverResult` / `SimulationRunResponse` have the same shape minus `solver_version`,
`governing_equations`, `assumptions`, `warnings`, `convergence` (those are unified-only fields).

## 7. Material schemas

```python
@dataclass(frozen=True)
class MaterialProperty:
    value: float
    unit: str
    source: str
    valid_range: tuple[float, float] | None = None
    notes: str | None = None

MATERIAL_LIBRARY: dict[str, dict[str, MaterialProperty]]
```
Materials defined: `concrete`, `steel`, `aluminum`, `granite`, `limestone` (each with a subset of
`density`, `thermal_conductivity`, `elastic_modulus`, `poisson_ratio`, `yield_strength`/`compressive_strength`).
Requesting an unknown material/property raises `MaterialNotFoundError` / `MaterialPropertyNotFoundError`
(422) — no silent default is ever substituted. `database/migrations/007_material_library.sql` mirrors
these same values in a queryable table for audit purposes only; solvers read `materials.py` at request time,
not the database table.

## 8. Boundary-condition schemas

```python
class BoundaryConditions(BaseModel):
    prescribed_temperature_c: float | None = None
    ambient_temperature_c: float | None = None
    heat_flux_w_m2: float | None = None
    convection_coefficient_w_m2k: float | None = None   # declared, NOT implemented by any solver yet
    heat_source_w_m3: float | None = None               # >= 0
    axial_load_n: float | None = None
    transverse_load_n: float | None = None
    point_mass_kg: float | None = None                  # > 0
    spring_stiffness_n_m: float | None = None            # > 0
```
Each `EngineeringSolver.validate_boundary_conditions()` rejects any field its family does not use
(see `supported_boundary_conditions` per solver in §4/registry).

## 9. Job status model

Shared `queued -> running -> {completed | failed | cancelled}` state machine across two independent
job tables (`generation_jobs` for Module 1, `simulation_jobs` for Module 2):
```python
class SimulationStatus(str, Enum):
    QUEUED = "queued"; RUNNING = "running"; COMPLETED = "completed"
    FAILED = "failed"; CANCELLED = "cancelled"
```
`generation_jobs.status` additionally allows `partial_failure` (batch generation, per-item failures).
Both tables carry: `progress_percent` (0-100, DB CHECK constraint), `error_code`, `safe_error_message`
(client-safe text only, never a raw exception/stack trace), `idempotency_key` (unique per user when
supplied), `created_at`/`started_at`/`finished_at`/`updated_at`.

## 10. Persistence repository interfaces

`PersistenceRepository` (ABC, `app/core/repository.py`) — abstract methods grouped by table:
- experiments: `create_experiment`, `get_experiment`
- design_models: `create_design_model`, `update_design_model_status`, `list_design_models_for_experiment`
- design_files: `record_design_file`, `get_design_file`, `list_design_files_for_experiment`
- generation_jobs: `create_job`, `get_job_by_idempotency_key`, `get_job`, `count_active_jobs_for_user`, `update_job`
- simulation_jobs: `create_simulation_job`, `get_simulation_job`, `get_simulation_job_by_idempotency_key`, `count_active_simulation_jobs_for_user`, `update_simulation_job`
- simulation_inputs: `record_simulation_input`, `get_simulation_input`
- simulation_results: `record_simulation_result`, `get_simulation_result`

Two concrete implementations: `SupabaseRepository` (production, used when `SUPABASE_URL`+`SUPABASE_KEY`
are set) and `LocalSQLiteRepository` (deterministic, on-disk SQLite, used for dev/tests). Resolved via
`get_repository()` factory (fresh instance per call, no caching).

**Do not confuse this with `app/core/persistence.py`'s `PersistenceService`** — that is a separate,
older, narrower class used only by the legacy `/api/pipeline/*` flow, and its column names do **not**
match the current authoritative schema (see §24, known bug).

## 11. File storage interfaces

`FileStorage` (ABC, `app/core/storage.py`):
```python
class FileStorage(ABC):
    def validate_object_key(self, object_key: str) -> None: ...     # concrete, fail-closed
    @abstractmethod
    def save_file(self, object_key: str, source_path: Path) -> None: ...
    @abstractmethod
    def file_exists(self, object_key: str) -> bool: ...
    @abstractmethod
    def open_bytes(self, object_key: str) -> bytes: ...
    @abstractmethod
    def delete_file(self, object_key: str) -> None: ...
    @abstractmethod
    def create_download_response(self, object_key: str, download_filename: str, media_type: str) -> Response: ...
    def calculate_checksum(self, source_path: Path) -> str: ...     # concrete, sha256
```
Implementations: `LocalFileStorage(root_dir)` (dev/CI) and `SupabaseStorage(client, bucket)` (production,
bucket name from `settings.SUPABASE_STORAGE_BUCKET`, default `"design-files"`). Object keys are **always**
server-built via `build_object_key(user_id, experiment_id, design_id, filename)` — never accepted from a
client — in the fixed shape `users/{user_id}/experiments/{experiment_id}/designs/{design_id}/{filename}`.
`SupabaseStorage` always streams bytes through the backend; it never returns a public/unauthenticated URL.

## 12. Authentication and ownership model

- Bearer JWT only (`OAuth2PasswordBearer`, `auto_error=False`), decoded in `app/core/auth.py` with
  `python-jose`, secret from `JWT_SECRET_KEY` or `SUPABASE_JWT_SECRET`, algorithm `settings.JWT_ALGORITHM`
  (default `HS256`).
- `get_current_user()` returns `{"id": <sub claim>, "email", "role" (default "researcher"), "claims"}`.
  Missing/invalid token -> 401. Missing secret configuration -> 500 (fail closed, checked again at
  startup in `main.py`'s `validate_startup_environment`).
- Every router (`module1`, `module1_jobs`, `module2` legacy + unified, `module3`, `pipeline`) declares
  `dependencies=[Depends(get_current_user)]` at the router level — every route requires a valid bearer token.
- **Ownership enforcement happens in the repository layer's read methods** (`job.user_id != user_id` ->
  404, not 403 — fail-closed so an unauthorized caller cannot distinguish "not found" from "not yours").
  This is explicit application-level enforcement, not reliance on Supabase RLS alone: `SupabaseRepository`
  uses one shared service client without per-request auth binding, so **RLS is defense-in-depth here, not
  the sole enforcing control** (see §21 for what this implies about JWT verification vs. RLS assumptions).

## 13. Current database table names and important columns

All tables live in schema `public`, RLS enabled on every one, owner-scoped policies unless noted.

| Table | Key columns | Notes |
|---|---|---|
| `profiles` | `id` (PK, FK->`auth.users`), `full_name`, `role` | 1:1 with Supabase auth user |
| `experiments` | `id`, `user_id` (FK->profiles), `name`, `status`, `input_specification` jsonb, `application_version` | |
| `design_models` | `id`, `experiment_id`, `user_id`, `geometry_family`, `parameters` jsonb, `units` jsonb, `variation_index`, `generation_status`, `cadquery_version` | unique `(experiment_id, variation_index)` |
| `design_files` | `id`, `design_model_id`, `experiment_id`, `user_id`, `file_format`, `storage_provider`, `object_key` (unique), `file_size_bytes`, `checksum_sha256`, `media_type` | |
| `generation_jobs` | `id`, `experiment_id`, `user_id`, `job_type`, `status`, `requested_count`, `completed_count`, `failed_count`, `progress_percent`, `error_code`, `safe_error_message`, `idempotency_key` | status CHECK enum; unique `(user_id, idempotency_key)` where not null |
| `simulation_jobs` | `id`, `experiment_id`, `design_id`, `user_id`, `solver_id`, `status`, `progress_percent`, `idempotency_key`, `error_code`, `safe_error_message` | mirrors `generation_jobs` |
| `simulation_inputs` | `simulation_id` (PK, FK->simulation_jobs), `material_name`, `material_properties` jsonb, `units`, `initial_conditions`, `boundary_conditions`, `numerical_settings` | immutable 1:1 snapshot |
| `simulation_results` | `simulation_id` (PK, FK->simulation_jobs), `solver_id`, `solver_version`, `governing_equations`, `assumptions`, `warnings`, `converged`, `residual`, `iteration_count`, `tolerance`, `summary_metrics`, `field_values`, `hotspot_node_ids`, `result_object_keys` | immutable 1:1 result |
| `material_library` | `id`, `material_name`, `property_name`, `value`, `unit`, `source`, `valid_range_min/max`, `notes` | unique `(material_name, property_name)`; read-only reference data, SELECT policy requires `auth.role() = 'authenticated'` only (not owner-scoped — intentional, non-user-owned data) |

**Not present in any migration:** a `simulation_metrics` table — but `app/core/persistence.py`
(`PersistenceService.store_simulation_metrics`) writes to a table by that name. This code path is only
reachable via the legacy `/api/pipeline/*` routes and will fail against the live schema (see §24).

## 14. Migration order and latest migration

Canonical source: `database/migrations/001_initial_schema.sql` through `007_material_library.sql`
(applied, in order, to the live Supabase project). Supabase-CLI-integration mirror (same SQL, verbatim):
`backend/supabase/migrations/20250101000001_initial_schema.sql` through
`backend/supabase/migrations/20250101000007_material_library.sql`, plus one additional no-op migration
already applied to trigger the first deploy: `backend/supabase/migrations/20260720070000_trigger_initial_production_deploy.sql`.
**Latest migration: `20260720070000_trigger_initial_production_deploy.sql` (no-op, adds no schema).**
The two migration directories are kept in sync intentionally (`database/migrations/README.md` documents
this); `backend/supabase/migrations/` is what the Supabase GitHub Integration actually applies.

## 15. Simulation API routes

Legacy (`/api/simulate`, in `module2_simulation/router.py`):
- `POST /api/simulate/advisor` — recommend analyses for a geometry/model type
- `POST /api/simulate/run` — synchronous; only `analysis_type=thermal` is supported (others raise 501)
- `POST /api/simulate/run-async` — Celery-queued; returns `{job_id, status}`
- `GET /api/simulate/jobs/{job_id}` — raw Celery `AsyncResult` status (no DB persistence, no ownership check)

Unified (`/api/simulations`, Phase C8):
- `GET /api/simulations/capabilities` — full `SOLVER_REGISTRY` dump
- `POST /api/simulations/recommend` — registry-backed recommendation
- `POST /api/simulations` (202) — create + queue a job (idempotency-key header supported); rejects
  non-`REAL` solvers (422) and unknown materials (422); 429 on per-user concurrency limit
- `GET /api/simulations/{simulation_id}` — job status, owner-only (404 if not found/not owned)
- `POST /api/simulations/{simulation_id}/cancel` — cooperative cancel, owner-only
- `GET /api/simulations/{simulation_id}/results` — job status + persisted result payload, owner-only

## 16. Module 3 API routes

- `POST /api/analyze/full-report` — the only Module 3 route. Stateless: takes `design_results` (list of
  `{design_id, params, metrics}`) directly in the request body, runs `cluster_designs` +
  `build_correlation_matrix` + `synthesize_report`, returns `{clusters, correlation, insights}`. **No
  database persistence, no ownership model, no direct call into Module 1 or Module 2** — integration with
  those modules today happens only through the separate legacy `/api/pipeline/*` flow
  (`app/pipeline_service.py`), which calls Module 1 generation, Module 2 simulation, and these same Module 3
  functions in one orchestrated request.

## 17. Example valid JSON request and response payloads

`POST /api/simulations` request:
```json
{
  "solver_id": "thermal_conduction_v1",
  "material": {"name": "steel"},
  "geometry": {"dimension": "1d", "length_m": 2.0, "num_elements": 20},
  "boundary_conditions": {"prescribed_temperature_c": 100.0, "heat_flux_w_m2": 500.0},
  "numerical_settings": {"max_iterations": 300, "tolerance": 1e-5}
}
```
`202` response (`SimulationJobResponse`):
```json
{
  "simulation_id": "b6d4e6b0-...-9a3c",
  "experiment_id": null,
  "design_id": null,
  "solver_id": "thermal_conduction_v1",
  "status": "queued",
  "progress_percent": 0,
  "error_code": null,
  "safe_error_message": null,
  "created_at": "2026-07-20T07:00:00+00:00",
  "started_at": null,
  "finished_at": null
}
```
`GET /api/simulations/{id}/results` (`SimulationResultsResponse`, once completed):
```json
{
  "simulation_id": "b6d4e6b0-...-9a3c",
  "experiment_id": null,
  "design_id": null,
  "solver_id": "thermal_conduction_v1",
  "status": "completed",
  "progress_percent": 100,
  "error_code": null,
  "safe_error_message": null,
  "created_at": "2026-07-20T07:00:00+00:00",
  "started_at": "2026-07-20T07:00:01+00:00",
  "finished_at": "2026-07-20T07:00:03+00:00",
  "result": {
    "solver_id": "thermal_conduction_v1",
    "solver_version": "1.0.0",
    "governing_equations": ["Steady-state heat conduction: k * Laplacian(T) + q = 0"],
    "assumptions": [],
    "warnings": [],
    "convergence": {"converged": true, "iterations": 42, "residual": 1e-6, "tolerance": 1e-5},
    "summary_metrics": {"max_temperature_c": 100.0, "avg_temperature_c": 62.3, "min_temperature_c": 24.6},
    "field_values": [100.0, 95.2, 90.1],
    "hotspot_node_ids": [0]
  }
}
```

## 18. Exception and error-response conventions

- Deliberate domain errors are raised as plain Python exceptions in service/registry/solver modules
  (`SolverValidationError`, `UnknownSolverError`, `UnsupportedCapabilityError`, `MaterialNotFoundError`,
  `MaterialPropertyNotFoundError`, `SimulationNotFoundError`, `SimulationRateLimitError`,
  `UnsupportedAnalysisError`, `StorageError`) and translated to an `HTTPException` **only at the router
  layer** (never inside a service/solver) — status codes are consistent: `404` not-found/not-owned
  (fail-closed, never distinguishes "doesn't exist" from "not yours"), `422` validation/unknown
  material/unknown or unsupported solver, `429` rate limit, `501` legacy unsupported analysis type.
- `app/main.py` registers a catch-all `@app.exception_handler(Exception)` that logs the full traceback
  server-side but always returns a generic `{"detail": "Internal server error"}` with status 500 — no
  stack trace or internal exception text is ever leaked to a client.
- Persisted job/simulation error fields (`error_code`, `safe_error_message`) are explicitly named "safe"
  — only client-presentable text is stored there, never a raw exception message.

## 19. Test markers and exact test commands

Markers (from `backend/pytest.ini`): `unit`, `integration`, `e2e`, `benchmark`, `external`.
- `unit`: fast, isolated, may mock external deps (e.g. stubbed CadQuery) — never proof the real CAD kernel works.
- `integration`: real CadQuery/OCP kernel required, FastAPI app driven via `TestClient`, no stubs.
- `e2e`: real FastAPI app started as a live server process, called over real HTTP.
- `benchmark`: numerical solver validation against an analytical solution or grid-convergence check.
- `external`: requires live third-party credentials (e.g. real Supabase project) — skipped, never marked
  passed, when credentials are absent.

Exact commands (run from `backend/`, using the project's own virtualenv):
```
python -m pytest -m "unit or integration or e2e or benchmark" -q
python -m pytest -m external -q          # only runs with live Supabase credentials configured
```
Use `python -m pytest`, not bare `pytest` — the bare console-script entry point does not add the
current working directory to `sys.path`, which breaks `app` package imports in some environments (this
was an actual CI bug, fixed by switching the workflow to `python -m pytest`).

Latest run at this commit: **71 passed, 3 deselected** (`external` tests deselected — no live Supabase
credentials in this environment), 26 warnings (deprecation warnings only, no test failures).

## 20. Docker and dependency constraints

`backend/Dockerfile` (full, current):
```dockerfile
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update \
    && apt-get install --no-install-recommends -y libgl1 libglib2.0-0 libxrender1 libxext6 libsm6 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -c "import cadquery as cq; model = cq.Workplane('XY').box(1, 1, 1); print('CadQuery smoke test passed:', model.val().Volume())"
COPY app ./app
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
```
Pinned dependencies (`backend/requirements.txt`): `fastapi==0.115.0`, `uvicorn[standard]==0.30.6`,
`pydantic==2.9.2`, `pydantic-settings==2.5.2`, `cadquery==2.4.0`, `numpy==1.26.4`, `pandas==2.2.2`,
`scikit-learn==1.5.1`, `scipy==1.17.1`, `anthropic==0.34.2`, `supabase==2.7.4`, `python-multipart==0.0.9`,
`celery==5.4.0`, `redis==5.0.8`, `python-jose[cryptography]==3.3.0`, `pytest==8.3.2`, `httpx==0.27.0`.
Python: **3.11** (Docker image `python:3.11-slim`; local dev venv is `3.11.15`).
The X11/OpenGL runtime libs (`libgl1`, `libglib2.0-0`, `libxrender1`, `libxext6`, `libsm6`) are required
by CadQuery's OCP kernel at import time and must not be removed from the image.
`backend/render.yaml` declares a **different** (`env: python`, native, not Docker) Blueprint config than
the actual live Render service (which uses the Dockerfile per the dashboard) — this file is not the
active driver of the real deployment; do not assume it reflects production.

## 21. Known Render resource limitations

- Free-tier Render web service: limited CPU/RAM, cold starts after idle, no guaranteed persistent disk
  (any local-filesystem state, including `LocalFileStorage`/`LocalSQLiteRepository` data if ever used in
  production, does **not** survive a restart/redeploy — production must run with `SUPABASE_URL`/`SUPABASE_KEY`
  set so `SupabaseRepository`/`SupabaseStorage` are used instead).
- No Celery worker process is defined in `render.yaml`/the Docker setup observed in this repo — `/api/*/run-async`
  and `/api/simulations` (POST) dispatch to Celery (`*.delay(...)`), but whether a worker process is
  actually running in the deployed environment could not be verified from this workspace (no Render API
  access — see §22 in the integration manifest / §6 production status doc). If no worker is running,
  queued jobs will never transition out of `queued`.
- No Redis add-on/managed instance was found configured in this repository; `CELERY_BROKER_URL`/
  `CELERY_RESULT_BACKEND` default to `redis://localhost:6379/...`, which will not resolve inside a Render
  web service container without an explicit external Redis URL set via environment variable.

## 22. Files an external coding agent MAY modify

- `backend/app/module2_simulation/solvers/` (new solver files/classes, e.g. adding CFD/acoustic/EM/coupled)
- `backend/app/module2_simulation/solver_registry.py` (registering new solver entries — do not change
  existing `REAL` entries' semantics)
- `backend/app/module2_simulation/schemas.py`, `materials.py` (additive changes only — new optional
  fields, new materials/properties)
- `backend/app/module2_simulation/service.py`, `tasks.py`, `router.py` (wiring new solvers in, additively)
- `backend/app/module3_analysis/` (new analytics, additively)
- `backend/tests/` (new test files; may add new markers' worth of tests under existing marker names)
- `docs/` (this handoff and related documents)
- New files anywhere under `backend/app/` that implement genuinely new, additive capability

## 23. Files an external coding agent MUST NOT modify

- `backend/Dockerfile`, `backend/render.yaml`, `.github/workflows/deploy.yml` (deployment configuration)
- `database/migrations/*.sql`, `backend/supabase/migrations/*.sql`, `backend/supabase/config.toml`
  (already-applied migrations — see backward-compatibility requirement below; add new migrations
  instead of editing existing ones)
- `backend/app/core/auth.py`, `backend/app/core/config.py` (security-sensitive; changes here need
  explicit review, not an automated sprint)
- `backend/app/core/repository.py`'s existing `PersistenceRepository` abstract method signatures and
  `SupabaseRepository`/`LocalSQLiteRepository`'s existing method bodies (ownership/ID contracts other
  code depends on) — new methods may be added, existing ones must not change signature or semantics
- Anything under `frontend/`
- `.gitignore`, `LICENSE`, `GO_NO_GO_CHECKLIST.md`, `PROJECT_BOOTSTRAP.md`

## 24. Backward-compatibility requirements

- The legacy `/api/simulate/*` router and its schemas/solver (`BaseSolver`, `ThermalSolver`,
  `SimulationRunRequest`/`Response`) remain a deprecated compatibility surface. The authoritative
  `/api/pipeline/*` flow uses `EngineeringSolver`; do not reconnect it to the legacy path. Add unified
  (`EngineeringSolver`) solvers.
- New solver families must be added as new `SOLVER_REGISTRY` entries + new `EngineeringSolver` subclasses
  under `/api/simulations`, following the existing `implementation_status`/`validation_status` honesty
  convention: **never** register a formula-only estimator as `implementation_status=REAL`, and never
  describe a closed-form/empirical estimate as a "field solver". The bounded channel solver is real
  only within its declared fully developed laminar parallel-plate scope.
- **Known pre-existing bug, not to be silently "fixed" as a side effect of an unrelated task, but should
  be flagged/ticketed:** `app/core/persistence.py`'s `PersistenceService` (used only by the legacy
  `/api/pipeline/*` flow via `pipeline_service.py`) inserts into `experiments` using columns `owner_id`,
  `title`, `description` — the actual `experiments` table (migration 001) has `user_id`, `name` (no
  `description` column at all), and there is no `simulation_metrics` table in any migration despite
  `PersistenceService.store_simulation_metrics` targeting one. With production Supabase credentials now
  configured, calls through this path will raise a Postgrest error. This is pre-existing and out of scope
  for this handoff task to fix, but any new work must not build on top of `PersistenceService` — use
  `app.core.repository.get_repository()` instead, which matches the real, applied schema.
- **Known pre-existing gap:** `module1_design.schemas.GeometryType` declares `ARCH` and `DOME` members,
  but `cadquery_engine.GEOMETRY_BUILDERS` only implements `PYRAMID`, `TOWER`, `BRIDGE`. Requesting
  `arch`/`dome` reaches `generate_model()`'s `builder is None` check and raises `ValueError` (mapped to
  HTTP 400) — this is a missing implementation, not a bug in the dispatch logic itself.
