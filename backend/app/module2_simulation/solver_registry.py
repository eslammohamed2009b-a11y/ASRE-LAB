"""
Module 2 — Solver capability registry.

Single source of truth for what every solver family can and cannot
actually do. The API (`router.py` via `simulation_advisor.py` and
`service.py`) must never fabricate a result for a solver whose
`implementation_status` is not `real`, and must never claim a capability
this registry does not list.

Each entry is a `CapabilityEntry` (see `schemas.py`) so the exact same
typed model backs both the registry and the `/api/simulations/capabilities`
HTTP response - there is no separate, driftable "public" description.
"""
from __future__ import annotations

from app.module2_simulation.schemas import (
    CapabilityEntry,
    ImplementationStatus,
    SolverFamily,
    ValidationStatus,
)

# -- legacy registry (unchanged) ------------------------------------------------
# Still used by the legacy `/api/simulate/*` router, `service.py`, and the
# integrated Module1->2->3 pipeline (`app.pipeline_service`). This is a
# narrower, older concept ("is this analysis_type backed by a real solver at
# all") than the new `SOLVER_REGISTRY` below and is kept exactly as before so
# existing surface keeps working unmodified.
SOLVER_VALIDATION_STATUS: dict[str, str] = {
    "thermal": "validated_prototype",
    "structural": "unsupported",
    "wind_load": "unsupported",
}


def is_supported(analysis_type: str) -> bool:
    return SOLVER_VALIDATION_STATUS.get(analysis_type) == "validated_prototype"


class UnsupportedAnalysisError(Exception):
    """Raised when a client requests an analysis type with no validated solver."""

    def __init__(self, analysis_type: str) -> None:
        self.analysis_type = analysis_type
        super().__init__(
            f"Analysis type '{analysis_type}' has no validated numerical solver in this "
            "build. It is implemented only as a simplified closed-form placeholder formula "
            "(not a real FEA/CFD solution), so this API refuses to return it as a simulation "
            "result. See /api/simulate/advisor for planned capabilities."
        )


# -- new unified registry (Phase C2) ------------------------------------------------
SOLVER_REGISTRY: dict[str, CapabilityEntry] = {
    "thermal_conduction_v1": CapabilityEntry(
        solver_id="thermal_conduction_v1",
        family=SolverFamily.THERMAL,
        version="1.0.0",
        implementation_status=ImplementationStatus.REAL,
        validation_status=ValidationStatus.VALIDATED,
        governing_equations=[
            "Steady-state heat conduction: k * Laplacian(T) + q = 0",
        ],
        supported_dimensions=["1d", "3d"],
        geometry_limitations=(
            "3d mode: uniform cubic finite-difference grid (5-40 nodes/edge), Dirichlet "
            "boundary on all six faces only. 1d mode: uniform rod/slab discretization "
            "(2-500 nodes), supports Dirichlet-Dirichlet or Neumann(flux)-Dirichlet ends."
        ),
        supported_materials=["concrete", "steel", "aluminum", "granite", "limestone"],
        supported_boundary_conditions=[
            "prescribed_temperature_c (Dirichlet)",
            "heat_flux_w_m2 (Neumann, 1d only)",
            "heat_source_w_m3 (volumetric generation, 3d only)",
        ],
        required_inputs=["material", "geometry.dimension", "boundary_conditions", "numerical_settings"],
        output_metrics=["max_temperature_c", "avg_temperature_c", "min_temperature_c", "thermal_conductivity_w_mk"],
        known_limitations=[
            "No transient (time-dependent) conduction.",
            "No convection boundary condition yet (declared, not implemented).",
            "3d mode requires uniform cubic geometry; arbitrary CAD meshes are not consumed.",
        ],
        benchmark_references=[
            "tests/unit/test_thermal_solver_benchmark.py::test_zero_heat_source_converges_to_ambient_temperature "
            "(3d Laplace analytical limit)",
            "tests/integration/test_thermal_solver_v2_benchmark.py::test_1d_slab_matches_linear_analytical_profile "
            "(1d Dirichlet-Dirichlet analytical linear profile)",
            "tests/integration/test_thermal_solver_v2_benchmark.py::test_1d_prescribed_flux_matches_analytical_profile "
            "(1d Neumann-Dirichlet analytical linear profile)",
        ],
    ),
    "structural_linear_1d_v1": CapabilityEntry(
        solver_id="structural_linear_1d_v1",
        family=SolverFamily.STRUCTURAL,
        version="1.0.0",
        implementation_status=ImplementationStatus.REAL,
        validation_status=ValidationStatus.VALIDATED,
        governing_equations=[
            "Linear-elastic 1D bar: K_bar = (E*A/L) * [[1,-1],[-1,1]]",
            "Euler-Bernoulli beam: K_beam from EI/L^3 cubic Hermite stiffness matrix",
            "Global assembly + Dirichlet support elimination: K*u = F",
        ],
        supported_dimensions=["1d"],
        geometry_limitations=(
            "Single straight prismatic bar (axial) or cantilever beam (transverse), 1-500 "
            "elements. NOT arbitrary 2D/3D solids or frames - this is not general FEA."
        ),
        supported_materials=["concrete", "steel", "aluminum", "granite", "limestone"],
        supported_boundary_conditions=[
            "axial_load_n (bar, free end)",
            "transverse_load_n (cantilever beam, free end)",
            "fixed support at x=0 (built-in, not configurable in v1)",
        ],
        required_inputs=[
            "material", "geometry.length_m", "geometry.cross_section_area_m2",
            "geometry.moment_of_inertia_m4 (beam only)", "geometry.num_elements", "boundary_conditions",
        ],
        output_metrics=[
            "max_displacement_m", "max_stress_pa", "max_strain", "reaction_force_n",
            "factor_of_safety (only when yield_strength is known for the material)",
        ],
        known_limitations=[
            "1D bar/beam elements only - no plates, shells, or solid 3D elements.",
            "Linear-elastic material behavior only (no plasticity/buckling).",
            "Single fixed support at one end; no arbitrary boundary configurations yet.",
        ],
        benchmark_references=[
            "tests/integration/test_structural_solver_benchmark.py::test_axial_bar_matches_analytical_solution",
            "tests/integration/test_structural_solver_benchmark.py::test_cantilever_beam_matches_analytical_tip_deflection",
        ],
    ),
    "modal_eigen_1d_v1": CapabilityEntry(
        solver_id="modal_eigen_1d_v1",
        family=SolverFamily.MODAL,
        version="1.0.0",
        implementation_status=ImplementationStatus.REAL,
        validation_status=ValidationStatus.VALIDATED,
        governing_equations=[
            "SDOF mass-spring: omega_n = sqrt(k/m)",
            "Generalized eigenvalue problem: K*phi = omega^2 * M*phi (consistent mass/stiffness beam matrices)",
        ],
        supported_dimensions=["1d"],
        geometry_limitations=(
            "Single-degree-of-freedom mass-spring system, or a single straight cantilever "
            "beam discretized with Euler-Bernoulli beam elements (1-200 elements)."
        ),
        supported_materials=["concrete", "steel", "aluminum", "granite", "limestone"],
        supported_boundary_conditions=[
            "point_mass_kg + spring_stiffness_n_m (SDOF mode)",
            "fixed support at x=0 (cantilever beam mode, not configurable in v1)",
        ],
        required_inputs=["material", "geometry", "boundary_conditions"],
        output_metrics=["natural_frequencies_hz", "mode_ids"],
        known_limitations=[
            "Only the first N natural frequencies/modes of a single SDOF or cantilever "
            "beam model are computed - no arbitrary 3D modal analysis.",
            "No damping is modeled (undamped natural frequencies only).",
            "Mode shapes are returned as normalized eigenvector samples, not a mesh export file.",
        ],
        benchmark_references=[
            "tests/integration/test_modal_solver_benchmark.py::test_sdof_matches_analytical_frequency",
            "tests/integration/test_modal_solver_benchmark.py::test_cantilever_beam_first_mode_matches_analytical",
        ],
    ),
    "cfd_laminar_channel_2d_v1": CapabilityEntry(
        solver_id="cfd_laminar_channel_2d_v1",
        family=SolverFamily.CFD,
        version="1.0.0",
        implementation_status=ImplementationStatus.REAL,
        validation_status=ValidationStatus.VALIDATED,
        governing_equations=["Steady incompressible fully developed momentum: mu*d2u/dy2 = dp/dx", "Continuity: div(u)=0"],
        supported_dimensions=["2d"],
        geometry_limitations="Fully developed flow between infinite parallel plates on a 5-60 node rectangular grid.",
        supported_materials=["air", "water"],
        supported_boundary_conditions=["constant negative pressure gradient", "no-slip parallel walls"],
        required_inputs=["length_m", "height_m", "pressure_gradient_pa_m", "fluid material"],
        output_metrics=["maximum_velocity_m_s", "mean_velocity_m_s", "reynolds_number", "mass_conservation_residual_s_1"],
        known_limitations=[
            "Laminar Re < 2000 and fully developed internal flow only.",
            "No turbulence, compressibility, inlet development, obstacles, external aerodynamics, or arbitrary CAD mesh.",
        ],
        benchmark_references=["tests/integration/test_channel_flow_solver.py (analytical plane-Poiseuille profile and refinement)"],
    ),
    "acoustic_duct_1d_v1": CapabilityEntry(
        solver_id="acoustic_duct_1d_v1",
        family=SolverFamily.WAVE_ACOUSTIC,
        version="1.0.0",
        implementation_status=ImplementationStatus.REAL,
        validation_status=ValidationStatus.VALIDATED,
        governing_equations=["1D Helmholtz equation: d2p/dx2 + k^2 p = 0; k = 2*pi*f/c"],
        supported_dimensions=["1d"],
        geometry_limitations="Uniform straight 1D lossless duct, 4-500 elements; plane waves only.",
        supported_materials=["air"],
        supported_boundary_conditions=["driven pressure at x=0", "pressure-release or rigid termination"],
        required_inputs=["length_m", "source_frequency_hz", "source_pressure_pa", "right termination"],
        output_metrics=["max_pressure_amplitude_pa", "fundamental_resonance_hz", "wave_number_rad_m"],
        known_limitations=[
            "No arbitrary room/CAD geometry, higher transverse modes, losses, or nonlinear acoustics.",
        ],
        benchmark_references=["tests/integration/test_acoustic_solver.py (analytical sine-profile benchmark)"],
    ),
    "electrostatic_rectangular_2d_v1": CapabilityEntry(
        solver_id="electrostatic_rectangular_2d_v1",
        family=SolverFamily.ELECTROMAGNETIC,
        version="1.0.0",
        implementation_status=ImplementationStatus.REAL,
        validation_status=ValidationStatus.VALIDATED,
        governing_equations=["Electrostatic Poisson equation: Laplacian(V) = -rho/epsilon; E = -grad(V)"],
        supported_dimensions=["2d"],
        geometry_limitations="Uniform 2D rectangular grid, 5-60 nodes per direction; constant permittivity.",
        supported_materials=["air", "water"],
        supported_boundary_conditions=["fixed electric potential on all four boundaries"],
        required_inputs=["width_m", "height_m", "four boundary potentials", "optional bounded charge density"],
        output_metrics=["min_potential_v", "max_potential_v", "max_electric_field_v_m"],
        known_limitations=[
            "Electrostatic only: no magnetic field, time dependence, waves, dielectric interfaces, or arbitrary geometry.",
        ],
        benchmark_references=["tests/integration/test_electrostatic_solver.py (parallel-plate linear-potential benchmark)"],
    ),
    "coupled_multiphysics_v0": CapabilityEntry(
        solver_id="coupled_multiphysics_v0",
        family=SolverFamily.COUPLED,
        version="0.0.0",
        implementation_status=ImplementationStatus.PLANNED,
        validation_status=ValidationStatus.NOT_APPLICABLE,
        governing_equations=["Planned: two-way thermal-structural (thermoelastic) coupling (not implemented)"],
        supported_dimensions=[],
        geometry_limitations="Not implemented.",
        supported_materials=[],
        supported_boundary_conditions=[],
        required_inputs=[],
        output_metrics=[],
        known_limitations=[
            "No coupling exists yet. Required future work: pass thermal_conduction_v1 "
            "temperature fields as thermal-strain loads into structural_linear_1d_v1.",
        ],
        benchmark_references=[],
    ),
}


class UnknownSolverError(Exception):
    def __init__(self, solver_id: str) -> None:
        self.solver_id = solver_id
        super().__init__(f"Solver '{solver_id}' is not in the solver registry.")


class UnsupportedCapabilityError(Exception):
    """Raised when a client requests a solver whose implementation_status
    is not 'real' - i.e. there is no validated numerical engine backing
    it yet. The API must respond with a clear rejection, never a
    fabricated result."""

    def __init__(self, solver_id: str) -> None:
        entry = SOLVER_REGISTRY.get(solver_id)
        self.solver_id = solver_id
        status = entry.implementation_status.value if entry else "unknown"
        super().__init__(
            f"Solver '{solver_id}' has implementation_status='{status}' - no validated numerical "
            "result can be produced for it in this build. See /api/simulations/capabilities for details."
        )


def get_solver_metadata(solver_id: str) -> CapabilityEntry:
    if solver_id not in SOLVER_REGISTRY:
        raise UnknownSolverError(solver_id)
    return SOLVER_REGISTRY[solver_id]


def list_solvers(family: SolverFamily | None = None) -> list[CapabilityEntry]:
    entries = list(SOLVER_REGISTRY.values())
    if family is not None:
        entries = [e for e in entries if e.family == family]
    return entries


def is_available(solver_id: str) -> bool:
    entry = SOLVER_REGISTRY.get(solver_id)
    return entry is not None and entry.implementation_status == ImplementationStatus.REAL


def require_available(solver_id: str) -> CapabilityEntry:
    entry = get_solver_metadata(solver_id)
    if entry.implementation_status != ImplementationStatus.REAL:
        raise UnsupportedCapabilityError(solver_id)
    return entry
