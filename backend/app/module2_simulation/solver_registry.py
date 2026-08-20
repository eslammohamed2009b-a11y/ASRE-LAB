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

# Compatibility aliases are deliberately derived after SOLVER_REGISTRY is
# declared below.  They retain the old endpoint vocabulary but cannot act as a
# second scientific authority.
LEGACY_ANALYSIS_SOLVER_IDS = {"thermal": "thermal_conduction_v1"}


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
    "pyramid_thermal_conduction_v1": CapabilityEntry(
        solver_id="pyramid_thermal_conduction_v1",
        family=SolverFamily.THERMAL,
        version="1.0.0",
        implementation_status=ImplementationStatus.REAL,
        validation_status=ValidationStatus.PARTIALLY_VALIDATED,
        governing_equations=["Steady-state heat conduction in the masked domain: k * Laplacian(T) + q = 0"],
        supported_dimensions=["pyramid3d"],
        geometry_limitations=(
            "Solid square parametric pyramid only; height/base ratio 0.1-10; odd Cartesian grids "
            "9-41 nodes per bounding-box axis. Staircase mask, not a CAD or arbitrary finite-element mesh."
        ),
        supported_materials=["concrete", "steel", "aluminum", "granite", "limestone"],
        supported_boundary_conditions=[
            "prescribed_temperature_c (isothermal base)",
            "ambient_temperature_c (isothermal staircase sides/apex)",
            "heat_source_w_m3 (uniform volumetric source)",
        ],
        required_inputs=[
            "material", "geometry.base_length_m", "geometry.height_m", "geometry.grid_resolution",
            "boundary_conditions.prescribed_temperature_c", "boundary_conditions.ambient_temperature_c",
            "boundary_conditions.heat_source_w_m3", "numerical_settings",
        ],
        output_metrics=[
            "max_temperature_c", "avg_temperature_c", "min_temperature_c",
            "max_temperature_gradient_k_m", "estimated_domain_volume_m3", "integrated_heat_source_w",
        ],
        known_limitations=[
            "No transient conduction, convection, radiation, contact resistance, anisotropy, or temperature-dependent properties.",
            "Dirichlet base and exposed-surface temperatures only.",
            "Grid-mask volume and boundary location are resolution dependent; convergence evidence is required.",
            "This is not arbitrary CAD-mesh FEA and must not be interpreted as one.",
        ],
        benchmark_references=[
            "tests/unit/test_pyramid_thermal_solver.py::test_zero_source_equal_boundaries_matches_constant_analytical_solution",
            "tests/unit/test_pyramid_thermal_solver.py::test_geometry_change_changes_geometry_sensitive_result",
            "tests/unit/test_pyramid_thermal_solver.py::test_resolution_convergence_is_reported",
        ],
    ),
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

# Phase 3B authoritative CAD-mesh FEM capabilities.  These are deliberately
# separate from legacy bounded solvers; only this execution family consumes a
# PhysicsModelV1 and the Phase 3A TET4 artifact.
SOLVER_REGISTRY.update({
    "thermal_fem_3d_v1": CapabilityEntry(
        solver_id="thermal_fem_3d_v1", family=SolverFamily.THERMAL, version="1.0.0",
        implementation_status=ImplementationStatus.REAL, validation_status=ValidationStatus.PARTIALLY_VALIDATED,
        governing_equations=["-div(k grad(T)) = q, steady isotropic conduction"],
        numerical_method="Sparse TET4 Galerkin assembly with scipy.sparse direct solve",
        discretization="Authoritative Phase 3A SI tetra4 mesh; exact constant-gradient volume integration and triangle facet integration",
        supported_dimensions=["3d"], supported_geometry=["authoritative_cad_tetra4"],
        geometry_limitations="Closed solid, conforming TET4 domains only; no contact resistance, radiation, transient behavior, or anisotropy.",
        consumes_cad_geometry=True, consumes_authoritative_cad=True, required_mesh_dimension=3,
        accepted_element_types=["tetra4"], supported_domain_types=["solid", "fluid"],
        geometry_dependency_description="Consumes GeneratedMesh nodes, tetrahedra, semantic facet groups, mesh hash, and PhysicsModelV1 directly.",
        supported_materials=["materials with thermal_conductivity"], supported_boundary_conditions=["temperature", "heat_flux", "convection", "volumetric_heat_source"],
        required_inputs=["PhysicsModelV1", "matching authoritative mesh", "material snapshots", "semantic facet mappings"],
        output_metrics=["temperature field", "temperature gradient", "energy balance", "algebraic residual"],
        validity_envelope={"nodes": "<= 5000", "elements": "<= 20000", "mesh": "valid SI TET4"},
        convergence_requirements="Normalized sparse algebraic residual and independent global energy-balance error are both reported.",
        implementation_reference="app.module2_simulation.cad_fem_solvers.solve_thermal_fem_3d",
        known_limitations=["No transient heat capacity model.", "No nonconforming interfaces or thermal contact resistance."],
        benchmark_references=["tests/integration/test_cad_fem_3d.py::test_thermal_linear_cube_benchmark"],
    ),
    "structural_linear_elasticity_3d_v1": CapabilityEntry(
        solver_id="structural_linear_elasticity_3d_v1", family=SolverFamily.STRUCTURAL, version="1.0.0",
        implementation_status=ImplementationStatus.REAL, validation_status=ValidationStatus.PARTIALLY_VALIDATED,
        governing_equations=["small-strain linear elasticity: div(sigma)+b=0", "sigma = D epsilon"],
        numerical_method="Sparse TET4 displacement FEM with scipy.sparse direct solve",
        discretization="Authoritative Phase 3A SI tetra4 mesh; constant-strain TET4 and triangle-distributed surface loads",
        supported_dimensions=["3d"], supported_geometry=["authoritative_cad_tetra4"],
        geometry_limitations="Isotropic small-strain solids only; no contact, plasticity, geometric nonlinearity, buckling, or stress-singularity convergence claims.",
        consumes_cad_geometry=True, consumes_authoritative_cad=True, required_mesh_dimension=3,
        accepted_element_types=["tetra4"], supported_domain_types=["solid"],
        geometry_dependency_description="Consumes authoritative mesh and semantic CAD-to-facet mappings; no scalar geometry reconstruction.",
        supported_materials=["materials with elastic_modulus, poisson_ratio, density"], supported_boundary_conditions=["fixed_support", "displacement", "force", "pressure", "gravity"],
        required_inputs=["PhysicsModelV1", "matching authoritative mesh", "semantic supports and loads"],
        output_metrics=["displacement", "strain", "stress", "von Mises stress", "reactions", "equilibrium residual"],
        validity_envelope={"nodes": "<= 5000", "structural_dofs": "<= 15000", "poisson_ratio": "-1 < nu < 0.5"},
        convergence_requirements="Normalized sparse algebraic residual and independent global force-equilibrium residual are both reported.",
        implementation_reference="app.module2_simulation.cad_fem_solvers.solve_structural_fem_3d",
        known_limitations=["TET4 bending is mesh-sensitive.", "Surface force is uniformly distributed over target facets; no point-load shortcut."],
        benchmark_references=["tests/integration/test_cad_fem_3d.py::test_structural_axial_prism_benchmark_and_pressure_direction"],
    ),
    "modal_fem_3d_v1": CapabilityEntry(
        solver_id="modal_fem_3d_v1", family=SolverFamily.MODAL, version="1.0.0",
        implementation_status=ImplementationStatus.REAL, validation_status=ValidationStatus.PARTIALLY_VALIDATED,
        governing_equations=["K phi = lambda M phi", "f = sqrt(lambda)/(2*pi)"],
        numerical_method="Generalized sparse eigenproblem via scipy eigsh; bounded dense eigh only for small systems",
        discretization="Shared authoritative TET4 structural stiffness with consistent tetrahedral mass matrix",
        supported_dimensions=["3d"], supported_geometry=["authoritative_cad_tetra4"],
        geometry_limitations="Undamped linear modes of constrained isotropic solids only; no frequency response, damping, prestress, or participation factors.",
        consumes_cad_geometry=True, consumes_authoritative_cad=True, required_mesh_dimension=3,
        accepted_element_types=["tetra4"], supported_domain_types=["solid"],
        geometry_dependency_description="Reuses the authoritative TET4 structural assembly and support facet mappings.",
        supported_materials=["materials with elastic_modulus, poisson_ratio, density"], supported_boundary_conditions=["fixed_support", "displacement"],
        required_inputs=["PhysicsModelV1", "matching authoritative mesh", "requested_modes", "support constraints"],
        output_metrics=["natural frequencies", "mass-normalized mode shapes", "eigenpair residuals"],
        validity_envelope={"nodes": "<= 5000", "structural_dofs": "<= 15000", "modes": "1 to free_dofs-1"},
        convergence_requirements="Every returned mass-normalized generalized eigenpair includes its relative residual; non-positive modes fail.",
        implementation_reference="app.module2_simulation.cad_fem_solvers.solve_modal_fem_3d",
        known_limitations=["Unconstrained/rigid-body systems fail explicitly.", "No damping or modal participation factors."],
        benchmark_references=["tests/integration/test_cad_fem_3d.py::test_modal_constrained_modes_are_mass_normalized_and_refinement_changes_frequency"],
    ),
    "cfd_openfoam_laminar_internal_3d_v1": CapabilityEntry(
        solver_id="cfd_openfoam_laminar_internal_3d_v1", family=SolverFamily.CFD, version="1.0.0",
        implementation_status=ImplementationStatus.REAL, validation_status=ValidationStatus.PARTIALLY_VALIDATED,
        governing_equations=["Steady incompressible Navier-Stokes momentum", "Continuity: div(U)=0"],
        numerical_method="OpenFOAM Foundation 14 foamRun -solver incompressibleFluid; SIMPLE finite volume",
        discretization="Authoritative ASRE SI TET4 cells exported one-to-one as deterministic OpenFOAM polyMesh",
        supported_dimensions=["3d"], supported_geometry=["authoritative_cad_fluid_volume_tetra4"],
        geometry_limitations="Fixed-geometry internal fluid volume with one velocity inlet, one pressure outlet, and one no-slip wall group.",
        consumes_cad_geometry=True, consumes_authoritative_cad=True, required_mesh_dimension=3,
        accepted_element_types=["tetra4"], supported_domain_types=["fluid"],
        geometry_dependency_description="Consumes GeneratedMesh cells and semantic facets without independent OpenFOAM remeshing.",
        supported_materials=["single Newtonian fluid with authoritative density and dynamic_viscosity snapshots"],
        supported_boundary_conditions=["velocity_inlet", "pressure_boundary", "wall"],
        required_inputs=["matching CFD PhysicsModelV1", "explicit FLUID CAD volume", "authoritative ASRE TET4 mesh"],
        output_metrics=["cell-centered U", "cell-centered kinematic p", "surface phi", "residuals", "mass conservation"],
        validity_envelope={"flow": "steady incompressible Newtonian laminar single-phase isothermal internal", "mass_imbalance": "<= 1e-3"},
        convergence_requirements="Normal solver completion, SIMPLE residual convergence, finite reviewed fields, and normalized mass imbalance <= 1e-3.",
        implementation_reference="app.module2_simulation.solver_orchestrator.solve_openfoam_cfd_3d",
        known_limitations=["No turbulence, transient, compressible, multiphase, non-Newtonian, porous, rotating-frame, CHT, FSI, or combustion support.", "Analytical benchmark and three-mesh refinement remain pending Phase 3C-2B."],
        benchmark_references=["tests/integration/test_openfoam_cfd.py::test_real_openfoam_asre_channel_solve (real execution smoke; analytical benchmark pending)"],
    ),
})

# Kept beside the registry entries so the public contract is fully typed while
# avoiding duplicate execution metadata in individual solver classes.
_CONTRACT_DETAILS: dict[str, dict] = {
    "pyramid_thermal_conduction_v1": {"numerical_method": "Finite-difference iterative solution on a Cartesian masked grid", "discretization": "Odd 9-41 node Cartesian grid over a square-pyramid bounding box; staircase solid mask", "supported_geometry": ["parametric_square_pyramid"], "consumes_cad_geometry": False, "geometry_dependency_description": "Uses base_length_m and height_m to construct its own Cartesian square-pyramid mask; does not read STEP/STL or a CAD mesh.", "validity_envelope": {"base_length_m": "1e-3 to 1e3 m", "height_m": "1e-3 to 1e3 m", "grid_resolution": "odd integer 9 to 41"}, "convergence_requirements": "Resolution-dependent mask; report coarse/medium/fine grid refinement evidence before relying on a result.", "implementation_reference": "app.module2_simulation.solvers.pyramid_thermal_solver.PyramidThermalConductionSolver"},
    "thermal_conduction_v1": {"numerical_method": "1D: finite-difference assembled linear system with direct numpy.linalg.solve and algebraic residual. 3D: Gauss-Seidel-style iterative finite-difference 7-point-stencil solve.", "discretization": "1D uniform 2-500-node rod/slab; 3D uniform cubic 5-40-node grid with a 7-point stencil.", "supported_geometry": ["uniform_1d_rod_or_slab", "uniform_cubic_3d_domain"], "consumes_cad_geometry": False, "geometry_dependency_description": "Uses requested scalar dimensions and uniform-grid settings only; does not consume design artifacts or CAD meshes.", "validity_envelope": {"1d_nodes": "2 to 500", "3d_nodes_per_edge": "5 to 40", "geometry": "uniform rod/slab or cube only"}, "convergence_requirements": "1D is a direct solve. For 3D, maximum_iteration_update must be below the requested tolerance; actual iterations and final update norm are persisted, and reaching max_iterations first is reported as non-converged.", "implementation_reference": "app.module2_simulation.solvers.thermal_solver.ThermalConductionSolver"},
    "structural_linear_1d_v1": {"numerical_method": "Linear finite-element global stiffness assembly and direct linear solve", "discretization": "1-500 axial-bar or Euler-Bernoulli beam elements", "supported_geometry": ["straight_prismatic_bar", "straight_cantilever_beam"], "consumes_cad_geometry": False, "geometry_dependency_description": "Uses scalar length, area, inertia, and element count; does not consume CAD geometry or a mesh artifact.", "validity_envelope": {"num_elements": "1 to 500", "geometry": "single straight prismatic 1D member", "material": "linear elastic only"}, "convergence_requirements": "Refine element count for beam results when discretization error matters; no automatic study is supplied.", "implementation_reference": "app.module2_simulation.solvers.structural_solver.StructuralLinearSolver"},
    "modal_eigen_1d_v1": {"numerical_method": "Generalized eigenvalue solution for beam mode; closed-form SDOF frequency", "discretization": "SDOF or 1-200 Euler-Bernoulli beam elements", "supported_geometry": ["sdof_mass_spring", "straight_cantilever_beam"], "consumes_cad_geometry": False, "geometry_dependency_description": "Uses scalar SDOF properties or beam dimensions and element count; no CAD artifact is read.", "validity_envelope": {"beam_elements": "1 to 200", "geometry": "single SDOF or straight cantilever beam"}, "convergence_requirements": "Refine beam element count for eigenfrequency convergence; not applicable to closed-form SDOF mode.", "implementation_reference": "app.module2_simulation.solvers.modal_solver.ModalSolver"},
    "cfd_laminar_channel_2d_v1": {"numerical_method": "Second-order finite-difference fully developed momentum solve in y with no-slip boundary equations and direct numpy.linalg.solve; profile is replicated in x.", "discretization": "Uniform 5-60 by 5-60 structured rectangular grid; second-order y-direction finite-difference system.", "supported_geometry": ["infinite_parallel_plate_channel"], "consumes_cad_geometry": False, "geometry_dependency_description": "Uses requested channel height/length and regular-grid settings; no CAD or obstacle geometry is consumed.", "validity_envelope": {"grid_size": "5 to 60", "flow": "steady fully developed laminar Re < 2000"}, "convergence_requirements": "A direct linear solve is used; algebraic momentum and mass-conservation residuals must be <= 1e-9. Grid refinement is required only when sampled-field resolution matters.", "implementation_reference": "app.module2_simulation.solvers.channel_flow_solver.LaminarChannelFlowSolver"},
    "acoustic_duct_1d_v1": {"numerical_method": "Second-order finite-difference 1D Helmholtz solve with driven and pressure-release/rigid boundary equations; complex direct numpy.linalg.solve.", "discretization": "Uniform 4-500-element axial grid; second-order central finite differences.", "supported_geometry": ["uniform_straight_duct"], "consumes_cad_geometry": False, "geometry_dependency_description": "Uses scalar duct length and axial sampling; no CAD geometry is consumed.", "validity_envelope": {"num_elements": "4 to 500", "geometry": "uniform straight lossless 1D duct", "dispersion": "k*dx <= 0.5"}, "convergence_requirements": "The complex algebraic residual must be <= 1e-8 and k*dx <= 0.5; refine the axial grid when the dispersion bound is not met.", "implementation_reference": "app.module2_simulation.solvers.acoustic_solver.AcousticDuctSolver"},
    "electrostatic_rectangular_2d_v1": {"numerical_method": "Finite-difference iterative Poisson solution", "discretization": "Uniform 5-60 node rectangular grid in each direction", "supported_geometry": ["rectangular_2d_domain"], "consumes_cad_geometry": False, "geometry_dependency_description": "Uses scalar rectangular dimensions and grid resolution only; no CAD geometry or mesh is consumed.", "validity_envelope": {"grid_size": "5 to 60", "geometry": "uniform rectangular 2D domain", "permittivity": "constant"}, "convergence_requirements": "Use grid refinement when discretization error matters; no automatic convergence study is supplied.", "implementation_reference": "app.module2_simulation.solvers.electrostatic_solver.ElectrostaticRectangularSolver"},
    "coupled_multiphysics_v0": {"numerical_method": "not_applicable", "discretization": "not_applicable", "supported_geometry": [], "consumes_cad_geometry": False, "geometry_dependency_description": "not_applicable: planned solver has no execution path.", "validity_envelope": {"status": "not_applicable"}, "convergence_requirements": "not_applicable", "implementation_reference": "not_available"},
}
for _solver_id, _details in _CONTRACT_DETAILS.items():
    SOLVER_REGISTRY[_solver_id] = SOLVER_REGISTRY[_solver_id].model_copy(update=_details)

# Deprecated compatibility view.  This is computed from SOLVER_REGISTRY rather
# than maintained by hand.  Do not use it for new code.
SOLVER_VALIDATION_STATUS: dict[str, str] = {
    analysis_type: (
        "validated_prototype" if SOLVER_REGISTRY[solver_id].implementation_status == ImplementationStatus.REAL
        else "unsupported"
    )
    for analysis_type, solver_id in LEGACY_ANALYSIS_SOLVER_IDS.items()
}
SOLVER_VALIDATION_STATUS.update({"structural": "unsupported", "wind_load": "unsupported"})


def is_supported(analysis_type: str) -> bool:
    """Deprecated legacy query, derived from the authoritative registry."""
    solver_id = LEGACY_ANALYSIS_SOLVER_IDS.get(analysis_type)
    return bool(solver_id and SOLVER_REGISTRY[solver_id].implementation_status == ImplementationStatus.REAL)


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
