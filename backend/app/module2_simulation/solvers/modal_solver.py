"""
Module 2 — modal (vibration) solver (Phase C2 unified architecture).

Two real, distinct methods depending on the boundary conditions supplied:

- SDOF mass-spring: closed-form `omega_n = sqrt(k/m)` is exact for this
  idealization (there is nothing to discretize), so it is used directly
  rather than dressed up as an "FEA" result.
- Cantilever beam: a generalized eigenvalue problem `K*phi = omega^2*M*phi`
  built from the same Euler-Bernoulli beam stiffness matrix as
  `structural_solver.py` plus a consistent (not lumped) mass matrix, solved
  with `scipy.linalg.eigh(K, M)`. This is a real numerical eigen-solve, not
  a hardcoded analytical formula - the analytical Euler-Bernoulli first-mode
  formula is only used by the benchmark test to check the result.

No solver here fabricates results for higher-dimensional or damped systems;
those remain out of scope (see `solver_registry.SOLVER_REGISTRY`).
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.linalg import eigh

from app.module2_simulation import materials
from app.module2_simulation.schemas import CapabilityEntry, ConvergenceStatus, SimulationCreateRequest
from app.module2_simulation.solver_registry import get_solver_metadata
from app.module2_simulation.solvers.base_solver import EngineeringSolver, SolverValidationError, NumericalFieldOutput
from app.module2_simulation.solvers.structural_solver import _beam_element_stiffness


def _solve_sdof_natural_frequency(mass_kg: float, stiffness_n_m: float) -> float:
    """Exact closed-form undamped natural frequency of a single-degree-of-
    freedom mass-spring system, in Hz."""
    omega_n_rad_s = math.sqrt(stiffness_n_m / mass_kg)
    return omega_n_rad_s / (2.0 * math.pi)


def _beam_element_mass(density_kg_m3: float, area_m2: float, le: float) -> np.ndarray:
    """Consistent (not lumped) Euler-Bernoulli beam element mass matrix."""
    m = density_kg_m3 * area_m2 * le / 420.0
    return m * np.array(
        [
            [156.0, 22.0 * le, 54.0, -13.0 * le],
            [22.0 * le, 4.0 * le**2, 13.0 * le, -3.0 * le**2],
            [54.0, 13.0 * le, 156.0, -22.0 * le],
            [-13.0 * le, -3.0 * le**2, -22.0 * le, 4.0 * le**2],
        ]
    )


def _solve_cantilever_beam_modes(
    num_elements: int,
    length_m: float,
    moment_of_inertia_m4: float,
    elastic_modulus_pa: float,
    area_m2: float,
    density_kg_m3: float,
    num_modes: int,
) -> dict[str, Any]:
    """
    Real generalized eigenvalue solve `K*phi = omega^2*M*phi` for a
    cantilever (fixed at x=0) Euler-Bernoulli beam discretized with
    `num_elements` consistent-mass beam elements. Returns the first
    `num_modes` natural frequencies (Hz) and their eigenvectors.
    """
    n_nodes = num_elements + 1
    n_dofs = 2 * n_nodes
    le = length_m / num_elements

    k_e = _beam_element_stiffness(elastic_modulus_pa, moment_of_inertia_m4, le)
    m_e = _beam_element_mass(density_kg_m3, area_m2, le)

    K = np.zeros((n_dofs, n_dofs))
    M = np.zeros((n_dofs, n_dofs))
    for e in range(num_elements):
        dofs = [2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3]
        K[np.ix_(dofs, dofs)] += k_e
        M[np.ix_(dofs, dofs)] += m_e

    free_dofs = list(range(2, n_dofs))  # eliminate fixed support at node 0
    K_reduced = K[np.ix_(free_dofs, free_dofs)]
    M_reduced = M[np.ix_(free_dofs, free_dofs)]

    eigenvalues, eigenvectors = eigh(K_reduced, M_reduced)
    eigenvalues = np.clip(eigenvalues, a_min=0.0, a_max=None)
    natural_frequencies_hz = np.sqrt(eigenvalues) / (2.0 * math.pi)

    n_modes = min(num_modes, len(natural_frequencies_hz))
    return {
        "natural_frequencies_hz": natural_frequencies_hz[:n_modes],
        "mode_shapes": eigenvectors[:, :n_modes],
    }


class ModalSolver(EngineeringSolver):
    """Registered as `modal_eigen_1d_v1`."""

    solver_id = "modal_eigen_1d_v1"
    numerical_method = "Closed-form SDOF or generalized Euler-Bernoulli finite-element eigenvalue solve"

    def __init__(self) -> None:
        self._num_modes_computed = 0

    @property
    def capability_metadata(self) -> CapabilityEntry:
        return get_solver_metadata(self.solver_id)

    def validate_geometry(self, request: SimulationCreateRequest) -> None:
        geometry = request.geometry
        if geometry.dimension not in self.capability_metadata.supported_dimensions:
            raise SolverValidationError(
                f"modal_eigen_1d_v1 does not support geometry.dimension='{geometry.dimension}'"
            )
        bc = request.boundary_conditions
        is_sdof = bc.point_mass_kg is not None and bc.spring_stiffness_n_m is not None
        if is_sdof:
            return
        # Cantilever beam mode
        if geometry.length_m is None:
            raise SolverValidationError("cantilever beam modal solve requires geometry.length_m")
        if geometry.cross_section_area_m2 is None:
            raise SolverValidationError("cantilever beam modal solve requires geometry.cross_section_area_m2")
        if geometry.moment_of_inertia_m4 is None:
            raise SolverValidationError("cantilever beam modal solve requires geometry.moment_of_inertia_m4")
        num_elements = geometry.num_elements or 10
        if not (1 <= num_elements <= 200):
            raise SolverValidationError("geometry.num_elements out of supported range (1-200) for modal beam solve")

    def validate_material(self, request: SimulationCreateRequest) -> dict[str, Any]:
        bc = request.boundary_conditions
        if bc.point_mass_kg is not None and bc.spring_stiffness_n_m is not None:
            # SDOF mode needs no material property lookup.
            return {}
        try:
            elastic_modulus = materials.get_property(request.material.name, "elastic_modulus")
            density = materials.get_property(request.material.name, "density")
        except (materials.MaterialNotFoundError, materials.MaterialPropertyNotFoundError) as exc:
            raise SolverValidationError(str(exc)) from exc
        return {"elastic_modulus_pa": elastic_modulus.value, "density_kg_m3": density.value}

    def validate_boundary_conditions(self, request: SimulationCreateRequest) -> None:
        bc = request.boundary_conditions
        is_sdof = bc.point_mass_kg is not None or bc.spring_stiffness_n_m is not None
        if is_sdof and (bc.point_mass_kg is None or bc.spring_stiffness_n_m is None):
            raise SolverValidationError(
                "SDOF modal solve requires both boundary_conditions.point_mass_kg and "
                "boundary_conditions.spring_stiffness_n_m"
            )

    def prepare_model(self, request: SimulationCreateRequest, material_properties: dict[str, Any]) -> dict[str, Any]:
        return {
            "geometry": request.geometry,
            "boundary_conditions": request.boundary_conditions,
            **material_properties,
        }

    def generate_or_import_mesh(self, request: SimulationCreateRequest, model: dict[str, Any]) -> None:
        return None

    def solve(self, request: SimulationCreateRequest, model: dict[str, Any], mesh: None) -> dict[str, Any]:
        bc = model["boundary_conditions"]
        if bc.point_mass_kg is not None and bc.spring_stiffness_n_m is not None:
            frequency_hz = _solve_sdof_natural_frequency(bc.point_mass_kg, bc.spring_stiffness_n_m)
            self._num_modes_computed = 1
            return {"mode": "sdof", "natural_frequencies_hz": np.array([frequency_hz])}

        geometry = model["geometry"]
        num_elements = geometry.num_elements or 10
        result = _solve_cantilever_beam_modes(
            num_elements=num_elements,
            length_m=geometry.length_m,
            moment_of_inertia_m4=geometry.moment_of_inertia_m4,
            elastic_modulus_pa=model["elastic_modulus_pa"],
            area_m2=geometry.cross_section_area_m2,
            density_kg_m3=model["density_kg_m3"],
            num_modes=5,
        )
        self._num_modes_computed = len(result["natural_frequencies_hz"])
        return {"mode": "cantilever_beam", **result}

    def calculate_residual(self, raw_result: dict[str, Any]) -> float | None:
        # Direct eigen-solve (scipy.linalg.eigh) / closed-form SDOF - no
        # iterative residual to report.
        return None

    def check_convergence(self, raw_result: dict[str, Any]) -> ConvergenceStatus:
        return ConvergenceStatus(converged=True, iterations=1, residual=None, tolerance=None)

    def extract_metrics(self, raw_result: dict[str, Any]) -> tuple[dict[str, float], list[float], list[int]]:
        frequencies = raw_result["natural_frequencies_hz"]
        summary_metrics = {
            f"natural_frequency_mode_{i + 1}_hz": float(freq) for i, freq in enumerate(frequencies)
        }
        summary_metrics["fundamental_frequency_hz"] = float(frequencies[0])
        field_values = frequencies.tolist()
        hotspot_node_ids = list(range(len(frequencies)))
        return summary_metrics, field_values, hotspot_node_ids

    def return_assumptions(self) -> list[str]:
        return [
            "Undamped free vibration only (no damping model).",
            "SDOF mode: ideal point mass on a massless linear spring.",
            "Cantilever beam mode: prismatic Euler-Bernoulli beam, fixed support at x=0, "
            "consistent (not lumped) mass matrix.",
        ]

    def extract_field_outputs(self, raw_result, request):
        if raw_result["mode"] == "sdof":
            return []
        shapes = np.asarray(raw_result["mode_shapes"], dtype=float)
        return [NumericalFieldOutput(
            variable_name="mode_shape", unit="normalized", values=shapes,
            axes=[
                {"name": "reduced_degree_of_freedom", "unit": "index", "values": list(range(shapes.shape[0]))},
                {"name": "mode", "unit": "index", "values": list(range(1, shapes.shape[1] + 1))},
            ],
            grid_metadata={
                "normalization": "mass-normalized eigenvectors returned by scipy.linalg.eigh",
                "dof_order": "alternating transverse displacement and rotation, fixed-end DOFs removed",
            },
        )]
