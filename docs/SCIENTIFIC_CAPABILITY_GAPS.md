# ASRE-LAB Scientific Capability Gaps

Factual, per-physics-family assessment of what is actually implemented today
versus what is claimed. Source: `backend/app/module2_simulation/solver_registry.py`
(`SOLVER_REGISTRY`), the solver implementations under
`backend/app/module2_simulation/solvers/`, and `backend/app/module3_analysis/`.

**Convention used throughout this document:** a formula-only/closed-form/empirical
estimator (no discretized field, no mesh, no solved PDE) is never described as a
"field solver". It is labeled "empirical estimator" or "closed-form model" instead.

**Updated after Big Batch 2** (Draft PR #1, branch `codex/big-batch-solver-intelligence`,
not merged to `main`). Big Batch 2 is scoped to two things: (1) wiring the thermal,
structural and modal solvers' real numerical field output into persisted, secure
field-result storage with unified solver-result provenance/convergence metadata, and
(2) a new deterministic, evidence-grounded Module 3 engineering-intelligence pipeline.
It does not add any new physics solver family and does not change the
implementation/validation status of CFD, acoustic, electromagnetic or coupled
multiphysics below.

| Family | Current implementation | Governing equation | Numerical method | Dimensionality | Benchmark status | Validation status | Limitations | Missing work | Smallest defensible next step |
|---|---|---|---|---|---|---|---|---|---|
| **Thermal** | `ThermalConductionSolver` (`solvers/thermal_solver.py`), `solver_id=thermal_conduction_v1`, `implementation_status=REAL` | Steady-state heat conduction: `k * ∇²T + q = 0` | Finite-difference (uniform cubic grid, 3D mode) / finite-difference rod discretization (1D mode) | 1D (2-500 nodes) and 3D (uniform cubic grid, 5-40 nodes/edge) | `tests/unit/test_thermal_solver_benchmark.py` (3D zero-source -> ambient analytical limit); `tests/integration/test_thermal_solver_v2_benchmark.py` (1D Dirichlet-Dirichlet linear profile, 1D Neumann-Dirichlet linear profile); `tests/integration/test_solver_field_integration.py` (Big Batch 2: field persists correctly) | Validated (against analytical solutions) | Steady-state only (no transient/time-dependent conduction); `convection_coefficient_w_m2k` boundary condition is declared in the schema but not implemented by any solver; 3D mode requires a uniform cubic grid — arbitrary CAD-imported meshes are not consumed | Transient conduction (time-stepping); convection boundary condition; non-cubic/arbitrary 3D mesh ingestion | Add a convection (Robin) boundary condition to the existing steady-state 3D solver, benchmarked against a known fin/plate analytical convection solution, before attempting transient conduction |
| **Structural** | `StructuralLinearSolver` (`solvers/structural_solver.py`), `solver_id=structural_linear_1d_v1`, `implementation_status=REAL` | Linear-elastic 1D bar (`K = EA/L * [[1,-1],[-1,1]]`) and Euler-Bernoulli cantilever beam (cubic Hermite stiffness), global assembly + Dirichlet elimination `K*u=F` | Direct linear FE solve (single bar/beam element chain) | 1D only (single straight prismatic bar or cantilever beam, 1-500 elements) | `tests/integration/test_structural_solver_benchmark.py` (axial bar vs. analytical; cantilever beam tip deflection vs. analytical); `tests/integration/test_solver_field_integration.py` (Big Batch 2: field persists correctly) | Validated (against analytical solutions) | 1D bar/beam elements only — no plates, shells, solid 3D elements, or frames; linear-elastic only (no plasticity/buckling); single fixed support at one end only, not configurable; element stress is only calculated (and persisted) for the axial-bar mode — the cantilever-beam mode persists displacement only, no stress field | 2D/3D solid or shell FEA; multi-support configurations; nonlinear/plastic material behavior; buckling analysis; beam-mode stress recovery | Extend to a simple 2D frame (multiple bar/beam elements with arbitrary connectivity and support locations) before attempting solids/shells |
| **Modal / vibration** | `ModalSolver` (`solvers/modal_solver.py`), `solver_id=modal_eigen_1d_v1`, `implementation_status=REAL` | SDOF mass-spring (`ωₙ = sqrt(k/m)`); generalized eigenvalue problem `K*φ = ω²*M*φ` (consistent mass/stiffness beam matrices) | Direct eigenvalue solve (SDOF closed form) or generalized eigensolver (beam FE matrices) | 1D only (SDOF, or single cantilever beam, 1-200 elements) | `tests/integration/test_modal_solver_benchmark.py` (SDOF vs. analytical frequency; cantilever beam first mode vs. analytical); `tests/integration/test_solver_field_integration.py` (Big Batch 2: field persists correctly) | Validated (against analytical solutions) | Only first N natural frequencies of a single SDOF/cantilever beam — no arbitrary 3D modal analysis; undamped only (no damping model); **SDOF mode is scalar-only by design: it has no spatial eigenvector to persist, because a single-DOF mass-spring idealization has no discretized geometry to compute a mode shape over** — this must never be described as producing a mode-shape field; the cantilever-beam mode's normalized eigenvectors (from `scipy.linalg.eigh`) are persisted as a real mode-shape field, not a mesh export | Damped modal analysis; multi-DOF/2D-3D modal analysis; mesh-exportable mode shapes | Add damping (proportional/Rayleigh) to the existing 1D beam eigensolver, benchmarked against a known damped SDOF analytical solution |
| **CFD / wind** | `cfd_laminar_channel_2d_v1`, `implementation_status=REAL` | Fully developed incompressible momentum `μ d²u/dy² = dp/dx`; continuity `∇·u=0` | Second-order finite difference/direct linear solve | 2D rectangular field representation of fully developed parallel-plate flow | Plane-Poiseuille profile, mass residual, and grid refinement | Analytically validated locally | Laminar `Re < 2000`; no inlet development, turbulence, compressibility, obstacles, external aerodynamics, arbitrary CAD, or industrial accuracy | General internal/external CFD | Add a validated bounded developing-channel case before broader geometry |
| **Wave / acoustic** | `acoustic_duct_1d_v1`, `implementation_status=REAL` | Frequency-domain Helmholtz `d²p/dx² + k²p = 0` | Second-order complex finite difference/direct solve | Uniform straight 1D duct | Analytical quarter-sine pressure profile and resonance metric | Analytically validated locally | Lossless plane waves only; no arbitrary rooms/CAD, radiation, higher modes, or nonlinear acoustics | Losses and higher-dimensional domains | Add a benchmarked lossy impedance termination |
| **Electrostatic** | `electrostatic_rectangular_2d_v1`, `implementation_status=REAL` | Poisson `∇²V=-ρ/ε`; `E=-∇V` | Five-point finite difference/Gauss-Seidel | Uniform 2D rectangle | Linear parallel-plate potential and constant field | Analytically validated locally | Electrostatic only; constant permittivity; no magnetic field, waves, interfaces, or arbitrary geometry | Broader electrostatics and full electromagnetics | Add a dielectric-interface benchmark before any time-dependent Maxwell work |
| **Coupled multiphysics** | One-way workflow at `/api/couplings/thermal-structural`; the generic `coupled_multiphysics_v0` solver entry remains planned because no bidirectional solver exists | Steady heat conduction followed by linear thermal strain `ε_th=αΔT` | Sequential persisted solves; arithmetic-mean 1D temperature mapping | Compatible uniform 1D thermal/bar models only | Fully restrained bar stress `|σ|=EαΔT` | Analytically validated locally | One-way, sequential, steady, linear; no silent interpolation or bidirectional coupling | Spatial mapping and bidirectional coupling | Add conservative node-to-node mapping for matching 1D grids |
| **Module 3 analytics** | See the dedicated "Module 3 — Engineering Intelligence (Big Batch 2)" section below for the current, accurate description. This row previously described Module 3 as clustering/correlation-only; that description was stale and has been replaced. | N/A (statistical/deterministic pattern discovery and decision support, not a physics solver) | See section below | N/A | See section below | See section below | See section below | See section below | See section below |

## Solver-result provenance and convergence metadata (Big Batch 2)

Implemented and integrated: every `REAL` solver (`thermal_conduction_v1`, `structural_linear_1d_v1`,
`modal_eigen_1d_v1`) now writes through a single unified `SimulationResult` persistence shape in
`backend/app/core/repository.py` — `numerical_method`, `residual_history`, `validation_metadata`,
`elapsed_time_seconds`, `reproducibility_hash`, `source_design_id`, and `status`
(`queued`/`running`/`completed`/`failed`/`cancelled`) are stored identically across all three
families in both the SQLite and Supabase adapters, rather than each solver inventing its own ad hoc
result shape. Numerical field arrays themselves (temperature, displacement, stress, mode shape) are
written to the secure compressed field-result artifact storage introduced in Batch 1, addressed by
`variable_name`/`unit`/`axes`/`grid_metadata`, and only summary statistics (min/max/mean) are exposed
back into Module 3 datasets — the raw multidimensional arrays are never inlined into JSON responses.
Tested: `backend/tests/integration/test_solver_field_integration.py` exercises the solver → field
storage path end to end for all three real solver families.

## Module 3 — Engineering Intelligence (Big Batch 2)

Big Batch 2 replaces the previously-documented "clustering/correlation-only" description of Module 3
with a deterministic, persisted, evidence-grounded analysis pipeline (`app/module3_analysis/dataset.py`
+ `intelligence.py` + `service.py`, exposed under `/api/analyze`). The older `clustering.py`
(K-Means) / `correlation.py` (Pearson-only) / `synthesis.py` (optional Claude narrative) modules
still exist and remain reachable through `/api/analyze/full-report`, but they are a separate,
still-unvalidated code path (see "Scientifically limited / still planned" below) — they are not what
the rest of this section describes.

**Implemented (deterministic, code-verified, no LLM):**

- **Experiment dataset construction** — `dataset.build_experiment_dataset()` builds an
  `ExperimentDataset` strictly from persisted `design_models` / `simulation_jobs` /
  `simulation_results` / `field_results` records read through `PersistenceRepository`; callers cannot
  inject arbitrary data. Produces a SHA-256 `dataset_hash` over canonical JSON for reproducibility, and
  caps input size (5000 rows / 256 columns) to bound analysis cost.
- **Data-quality reporting** (`DatasetQualityReport`) — missing-value counts per column, duplicate
  simulation IDs (excluded), constant columns (flagged and excluded from correlation), incompatible
  units per column (flagged and excluded from correlation/sensitivity), non-numeric fields, and
  per-row `evidence_ids` (job/design/field-result IDs) for provenance back to the originating
  solver runs.
- **Descriptive statistics** — count, mean, median, sample standard deviation/variance, min/max,
  quartiles, interquartile range, coefficient of variation, per-column unit, and small-sample
  warnings (< 5 observations).
- **Pearson and Spearman correlation** — `scipy.stats.pearsonr`/`spearmanr` over pairwise-valid,
  non-constant, unit-compatible columns; every relationship carries its sample count, evidence
  simulation IDs, and an explicit **"correlation does not establish causation"** warning, repeated
  again as a dataset-level warning together with an uncorrected-multiple-comparisons warning.
  Effect-size labels ("negligible"/"small"/"moderate"/"large"/"very_large") describe association
  magnitude only, not physical importance — the code attaches this disclaimer directly.
- **Sensitivity estimation** — `regression_sensitivity()` fits a single **standardized
  (z-scored) linear regression** (`numpy.linalg.lstsq`) and reports standardized coefficients, R²,
  a condition number, and residual diagnostics (RMS/max/mean standardized residual). This is a
  first-order local linear sensitivity estimate, **not a variance-based/Sobol global sensitivity
  analysis**, and it is never described as one in the code or in this document. It raises explicit
  warnings for multicollinearity (condition number > 30 or rank-deficient design), small samples
  (< 20 rows), and poor linear fit (R² < 0.5).
- **Pareto-frontier analysis** — deterministic dominance filtering (`pareto_front()`) over
  user-declared objectives and maximize/minimize directions, with a warning that Pareto membership
  is scoped to the selected objectives only.
- **Weighted engineering ranking** — `weighted_ranking()` performs transparent min-max
  normalization and user-supplied weighted scoring with a per-objective contribution breakdown for
  every ranked design (no hidden weighting).
- **Structured, evidence-grounded recommendations** — `grounded_recommendations()` emits typed
  records (`ranked_candidate`, `observed_association`, `first_order_sensitivity_estimate`), each
  citing source simulation/design IDs, method, and a confidence qualifier
  (`bounded_by_dataset_quality`, `not_quantified`, `bounded_by_model_fit_and_diagnostics`) rather than
  free-text LLM generation, and each restates that observed associations are not evidence of
  causation.
- **Persistence** — the `experiment_analyses` table (SQLite local adapter and Supabase adapter in
  `repository.py`) stores `analysis_type`, `status`, `dataset_hash`, `configuration`, `result`,
  `warnings`, `source_design_ids`, `source_simulation_ids`, `data_quality`, `engine_version`, and
  `reproducibility_hash` for every run.
- **Migration `009_experiment_analyses.sql`** — adds the `experiment_analyses` table with
  owner-scoped row-level security (`user_id = auth.uid()`) and adds `status`, `numerical_method`,
  `residual_history`, `validation_metadata`, `elapsed_time_seconds`, `reproducibility_hash`, and
  `source_design_id` columns to `simulation_results`.
- **API** — `/api/analyze/experiments/{experiment_id}` (`POST` create + persist, `GET` list) and
  `/api/analyze/{analysis_id}` (`GET` one), all behind `get_current_user` and owner-scoped.

**Integrated:** dataset construction reads field-result summary statistics
(`field.<variable_name>.minimum/maximum/mean`) as ordinary dataset columns, so the real thermal
temperature, structural displacement/stress, and modal beam mode-shape field outputs described above
feed directly into Module 3 statistics/correlation/sensitivity/Pareto/ranking without any separate
hand-off step or caller-assembled payload.

The `/api/pipeline` orchestration uses this authoritative path end to end: persisted design
variants are linked to unified `thermal_conduction_v1` and `structural_linear_1d_v1` jobs,
genuine fields are persisted, and a deterministic `engineering_intelligence` analysis record
is created for the experiment. Because these solvers do not consume arbitrary CAD meshes,
pipeline simulations are explicitly disclosed 1D comparison scenarios with prescribed
reference boundary conditions. They are not full-geometry service-load predictions.
Unsupported wind/CFD requests fail without invoking the empirical legacy estimator.

**Tested:** `backend/tests/unit/test_engineering_intelligence.py` (statistics, correlation,
sensitivity, Pareto, ranking, and recommendation behavior, including warning generation and edge
cases), `backend/tests/integration/test_analysis_api.py` (persisted-analysis API), and
`backend/tests/integration/test_solver_field_integration.py` (solver → field-result → dataset
wiring).

**Scientifically limited (explicit, by design — not a defect):**

- Every correlation result is association only; causation is never claimed, and the code enforces
  this with warnings rather than relying on documentation alone.
- `regression_sensitivity` is a single standardized linear model, not a validated global/variance-based
  sensitivity method (e.g. Sobol indices); it will misrepresent nonlinear or interaction effects, and
  its own diagnostics flag when the linear fit is unreliable.
- No correction for multiple comparisons across correlation pairs (explicitly warned).
- Effect-size labels describe statistical magnitude only, never engineering significance.
- Analysis quality is bounded by whatever the persisted dataset actually contains — fewer than three
  valid simulations, or fewer than `max(5, features + 2)` rows for sensitivity, triggers an explicit
  reliability warning rather than being silently accepted.

**Still planned / not implemented:**

- The pre-existing `clustering.py` (K-Means) + `correlation.py` (Pearson-only matrix) +
  `synthesis.py` (optional Claude/LLM narrative) path under `/api/analyze/full-report` is unchanged
  by Big Batch 2 and remains unvalidated against a ground-truth dataset (no correctness benchmark).
- The reviewable feedback loop is implemented, but autonomous approval is intentionally prohibited;
  a user must accept every proposal before Module 1 generates a child design.
- Migrations 009 and 010 have not been applied against live staging or production Supabase as part
  of this batch and must not be treated as externally validated.

## Summary of implementation_status across the registry

| `solver_id` | `implementation_status` | `validation_status` |
|---|---|---|
| `thermal_conduction_v1` | real | validated |
| `structural_linear_1d_v1` | real | validated |
| `modal_eigen_1d_v1` | real | validated |
| `cfd_laminar_channel_2d_v1` | real | validated |
| `acoustic_duct_1d_v1` | real | validated |
| `electrostatic_rectangular_2d_v1` | real | validated |
| `coupled_multiphysics_v0` | planned | not_applicable |

Six registered solver entries now have validated, real numerical methods. The generic coupled entry
remains planned because the implemented thermal-to-structural capability is a separate, explicitly
one-way sequential workflow rather than a bidirectional `EngineeringSolver`.

## Backend completion status (final-backend-completion draft)

The following areas remain honestly unfinished or externally unvalidated:

- CFD beyond bounded fully developed laminar parallel-plate channel flow.
- Acoustic simulation beyond a straight lossless 1D plane-wave duct.
- Electromagnetics beyond bounded 2D electrostatics.
- Coupling beyond one-way sequential steady linear thermal-to-structural mapping.
- Autonomous design approval, which is intentionally not implemented.
- Production Redis/Celery worker validation (not run as part of this batch).
- Live external Supabase integration tests requiring real credentials (skip locally without them).
- Staging/production application of Migrations 009 and 010.
- UI/frontend work, which is intentionally outside the current backend-completion plan.

This work remains an unmerged draft branch and must not be treated as production code.
