"""
Module 2 — typed engineering schemas for the unified multi-physics
simulation architecture (see `solver_registry.py` for the capability
metadata these requests/results are validated against).

Deliberately explicit/typed fields instead of unrestricted dictionaries
wherever the shape of the data is known ahead of time. `boundary_conditions`
still exposes a flat set of optional named fields (rather than one giant
dict) because different solver families legitimately need different
subsets of them; each solver's `validate_boundary_conditions` decides
which of these it actually supports and rejects the rest with a clear
error, never a silent default that masks a missing/invalid input.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


# -- legacy schemas (unchanged) ------------------------------------------------
# Still used by the legacy `/api/simulate/*` router and the integrated
# Module1->2->3 pipeline (`app.pipeline_service`). Kept exactly as before so
# that existing surface keeps working unmodified; new work should use the
# typed schemas further down this file instead.
class AnalysisType(str, Enum):
    THERMAL = "thermal"
    STRUCTURAL = "structural"
    WIND_LOAD = "wind_load"


class AdvisorRequest(BaseModel):
    model_type: str = Field(min_length=2)


class AdvisorResponse(BaseModel):
    recommended: list[str]
    supported: list[str] = Field(
        default_factory=list,
        description=(
            "Subset of `recommended` that has a validated numerical solver today "
            "(currently only 'thermal'). The rest are advisory/planned only."
        ),
    )


class SimulationRunRequest(BaseModel):
    design_id: str = "unknown"
    geometry_type: str = "tower"
    analysis_type: AnalysisType
    material: str = "concrete"
    boundary_conditions: dict = Field(default_factory=dict)


class SimulationRunResponse(BaseModel):
    analysis_type: str
    design_id: str
    summary_metrics: dict[str, float]
    field_values: list[float]
    hotspot_node_ids: list[int]


class SolverFamily(str, Enum):
    THERMAL = "thermal"
    STRUCTURAL = "structural"
    MODAL = "modal"
    CFD = "cfd"
    WAVE_ACOUSTIC = "wave_acoustic"
    ELECTROMAGNETIC = "electromagnetic"
    COUPLED = "coupled"


class ImplementationStatus(str, Enum):
    REAL = "real"
    PROTOTYPE = "prototype"
    PLANNED = "planned"


class ValidationStatus(str, Enum):
    VALIDATED = "validated"
    PARTIALLY_VALIDATED = "partially_validated"
    UNVALIDATED = "unvalidated"
    NOT_APPLICABLE = "not_applicable"


class RecommendationStatus(str, Enum):
    AVAILABLE = "available"
    EXPERIMENTAL = "experimental"
    UNSUPPORTED = "unsupported"
    PLANNED = "planned"


class SimulationStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Quantity(BaseModel):
    value: float
    unit: str


class Geometry(BaseModel):
    """Minimal geometry description a solver needs. `dimension` gates which
    solvers may even attempt the request (see each solver's
    `supported_dimensions` in the registry)."""

    dimension: str = Field(description="'1d', '2d', or '3d' - must match a solver's supported_dimensions")
    length_m: float | None = Field(default=None, gt=0)
    width_m: float | None = Field(default=None, gt=0)
    height_m: float | None = Field(default=None, gt=0)
    cross_section_area_m2: float | None = Field(default=None, gt=0)
    moment_of_inertia_m4: float | None = Field(default=None, gt=0)
    num_elements: int | None = Field(default=None, ge=1, le=500)
    grid_resolution: int | None = Field(default=None, ge=5, le=60)
    grid_resolution_y: int | None = Field(default=None, ge=5, le=60)


class MaterialSelection(BaseModel):
    name: str = Field(min_length=2)


class InitialConditions(BaseModel):
    initial_temperature_c: float | None = None
    initial_displacement_m: float | None = None
    initial_velocity_m_s: float | None = None


class BoundaryConditions(BaseModel):
    prescribed_temperature_c: float | None = None
    ambient_temperature_c: float | None = None
    heat_flux_w_m2: float | None = None
    convection_coefficient_w_m2k: float | None = None
    heat_source_w_m3: float | None = Field(default=None, ge=0)
    axial_load_n: float | None = None
    transverse_load_n: float | None = None
    point_mass_kg: float | None = Field(default=None, gt=0)
    spring_stiffness_n_m: float | None = Field(default=None, gt=0)
    source_frequency_hz: float | None = Field(default=None, gt=0)
    source_pressure_pa: float | None = None
    acoustic_left_boundary: str | None = None
    acoustic_right_boundary: str | None = None
    potential_left_v: float | None = None
    potential_right_v: float | None = None
    potential_top_v: float | None = None
    potential_bottom_v: float | None = None
    potential_gradient_x_v_m: float | None = None
    charge_density_c_m3: float | None = None
    pressure_gradient_pa_m: float | None = None
    inlet_mean_velocity_m_s: float | None = Field(default=None, gt=0)
    thermal_delta_temperature_c: float | None = None
    thermal_expansion_coefficient_1_k: float | None = Field(default=None, gt=0)
    thermal_restraint: str | None = None


class NumericalSettings(BaseModel):
    max_iterations: int = Field(default=300, ge=1, le=5000)
    tolerance: float = Field(default=1e-5, gt=0, le=1.0)


class SimulationCreateRequest(BaseModel):
    solver_id: str = Field(min_length=2)
    experiment_id: str | None = None
    design_id: str | None = None
    material: MaterialSelection
    geometry: Geometry
    boundary_conditions: BoundaryConditions = Field(default_factory=BoundaryConditions)
    initial_conditions: InitialConditions = Field(default_factory=InitialConditions)
    numerical_settings: NumericalSettings = Field(default_factory=NumericalSettings)


class ConvergenceStatus(BaseModel):
    converged: bool
    iterations: int
    residual: float | None = None
    tolerance: float | None = None


class SimulationResultPayload(BaseModel):
    solver_id: str
    solver_version: str
    governing_equations: list[str]
    assumptions: list[str]
    warnings: list[str]
    convergence: ConvergenceStatus
    summary_metrics: dict[str, float]
    field_values: list[float]
    hotspot_node_ids: list[int]
    status: str = "completed"
    numerical_method: str = ""
    residual_history: list[float] = Field(default_factory=list, max_length=5000)
    validation_metadata: dict = Field(default_factory=dict)
    elapsed_time_seconds: float | None = Field(default=None, ge=0)
    reproducibility_hash: str = ""
    source_design_id: str | None = None
    source_simulation_id: str | None = None


class SimulationJobResponse(BaseModel):
    simulation_id: str
    experiment_id: str | None
    design_id: str | None
    solver_id: str
    status: SimulationStatus
    progress_percent: int
    error_code: str | None = None
    safe_error_message: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class SimulationResultsResponse(SimulationJobResponse):
    result: SimulationResultPayload | None = None


class CoordinateAxis(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    unit: str = Field(min_length=1, max_length=64)
    values: list[float] = Field(min_length=1, max_length=2000)


class FieldResultMetadataResponse(BaseModel):
    id: str
    simulation_id: str
    variable_name: str
    unit: str
    format: str
    format_version: str
    dimensions: int = Field(ge=1, le=4)
    axes: list[CoordinateAxis]
    array_shape: list[int]
    grid_metadata: dict
    checksum_sha256: str
    byte_size: int
    minimum: float
    maximum: float
    mean: float
    preview: list[float]
    reproducibility_hash: str
    created_at: str


class CapabilityEntry(BaseModel):
    solver_id: str
    family: SolverFamily
    version: str
    implementation_status: ImplementationStatus
    validation_status: ValidationStatus
    governing_equations: list[str]
    supported_dimensions: list[str]
    geometry_limitations: str
    supported_materials: list[str]
    supported_boundary_conditions: list[str]
    required_inputs: list[str]
    output_metrics: list[str]
    known_limitations: list[str]
    benchmark_references: list[str]


class CapabilitiesResponse(BaseModel):
    solvers: list[CapabilityEntry]


class RecommendRequest(BaseModel):
    geometry_category: str = Field(min_length=2)
    research_objective: str | None = None


class Recommendation(BaseModel):
    solver_id: str
    family: SolverFamily
    status: RecommendationStatus
    rationale: str


class RecommendResponse(BaseModel):
    recommendations: list[Recommendation]
