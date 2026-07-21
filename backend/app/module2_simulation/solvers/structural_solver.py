from __future__ import annotations

from typing import Any

import numpy as np

from app.module2_simulation import materials
from app.module2_simulation.schemas import CapabilityEntry, ConvergenceStatus, SimulationCreateRequest
from app.module2_simulation.solver_registry import get_solver_metadata
from app.module2_simulation.solvers.base_solver import (
    BaseSolver,
    EngineeringSolver,
    Mesh,
    SolverResult,
    SolverValidationError,
    NumericalFieldOutput,
)


class StructuralSolver(BaseSolver):
    analysis_type = "structural"

    def solve(self, mesh: Mesh, material: str, boundary_conditions: dict) -> SolverResult:
        n = len(mesh.nodes)
        load_n = float(boundary_conditions.get("axial_load_n", 1_000_000.0))
        area_m2 = float(boundary_conditions.get("section_area_m2", 10.0))
        stress_mpa = (load_n / max(area_m2, 1e-6)) / 1_000_000
        displacement_mm = stress_mpa * 0.02
        field = np.full(n, stress_mpa)

        return SolverResult(
            analysis_type=self.analysis_type,
            design_id=boundary_conditions.get("design_id", "unknown"),
            summary_metrics={
                "max_stress_mpa": float(stress_mpa),
                "max_displacement_mm": float(displacement_mm),
            },
            field_values=field.tolist(),
            hotspot_node_ids=list(range(min(5, n))),
        )


def _solve_axial_bar(
    num_elements: int,
    length_m: float,
    area_m2: float,
    elastic_modulus_pa: float,
    axial_load_n: float,
) -> dict[str, Any]:
    """
    Real 1D linear-elastic bar FEA: a chain of 2-node bar elements
    (`k_e = (E*A/Le) * [[1,-1],[-1,1]]`), fixed at node 0, with the axial
    load applied at the free end node. Assembles the global stiffness
    matrix, eliminates the fixed DOF, and solves `K*u=F` directly via
    `numpy.linalg.solve` (not a closed-form shortcut).
    """
    n_nodes = num_elements + 1
    le = length_m / num_elements
    k_e = (elastic_modulus_pa * area_m2 / le) * np.array([[1.0, -1.0], [-1.0, 1.0]])

    K = np.zeros((n_nodes, n_nodes))
    for e in range(num_elements):
        K[e : e + 2, e : e + 2] += k_e

    F = np.zeros(n_nodes)
    F[-1] = axial_load_n

    free_dofs = list(range(1, n_nodes))
    K_reduced = K[np.ix_(free_dofs, free_dofs)]
    F_reduced = F[free_dofs]
    u = np.zeros(n_nodes)
    u[free_dofs] = np.linalg.solve(K_reduced, F_reduced)

    residual = float(np.max(np.abs(K[np.ix_(free_dofs, free_dofs)] @ u[free_dofs] - F_reduced)))

    element_stress_pa = elastic_modulus_pa * (u[1:] - u[:-1]) / le
    reaction_force_n = float(-(K[0, :] @ u))

    return {
        "mode": "axial_bar",
        "displacements_m": u,
        "element_stress_pa": element_stress_pa,
        "reaction_force_n": reaction_force_n,
        "elastic_modulus_pa": elastic_modulus_pa,
        "residual": residual,
    }


def _beam_element_stiffness(elastic_modulus_pa: float, moment_of_inertia_m4: float, le: float) -> np.ndarray:
    ei_l3 = elastic_modulus_pa * moment_of_inertia_m4 / (le**3)
    return ei_l3 * np.array(
        [
            [12.0, 6.0 * le, -12.0, 6.0 * le],
            [6.0 * le, 4.0 * le**2, -6.0 * le, 2.0 * le**2],
            [-12.0, -6.0 * le, 12.0, -6.0 * le],
            [6.0 * le, 2.0 * le**2, -6.0 * le, 4.0 * le**2],
        ]
    )


def _solve_cantilever_beam(
    num_elements: int,
    length_m: float,
    moment_of_inertia_m4: float,
    elastic_modulus_pa: float,
    transverse_load_n: float,
) -> dict[str, Any]:
    """
    Real Euler-Bernoulli cantilever beam FEA using 2-node, 2-DOF-per-node
    (transverse displacement + rotation) cubic Hermite beam elements. Fixed
    at node 0 (both DOFs), transverse point load applied at the free end's
    transverse DOF. Cubic Hermite elements are exact for a prismatic
    Euler-Bernoulli beam under nodal loads, so the tip deflection matches
    the closed-form `P*L^3/(3*E*I)` to numerical precision regardless of
    element count - that agreement (checked in
    `tests/integration/test_structural_solver_benchmark.py`) is what
    validates this implementation, not a hardcoded formula.
    """
    n_nodes = num_elements + 1
    n_dofs = 2 * n_nodes
    le = length_m / num_elements
    k_e = _beam_element_stiffness(elastic_modulus_pa, moment_of_inertia_m4, le)

    K = np.zeros((n_dofs, n_dofs))
    for e in range(num_elements):
        dofs = [2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3]
        K[np.ix_(dofs, dofs)] += k_e

    F = np.zeros(n_dofs)
    F[-2] = transverse_load_n  # transverse DOF at the free end

    free_dofs = list(range(2, n_dofs))  # eliminate node 0's v and theta (fixed support)
    K_reduced = K[np.ix_(free_dofs, free_dofs)]
    F_reduced = F[free_dofs]
    u = np.zeros(n_dofs)
    u[free_dofs] = np.linalg.solve(K_reduced, F_reduced)

    residual = float(np.max(np.abs(K[np.ix_(free_dofs, free_dofs)] @ u[free_dofs] - F_reduced)))

    transverse_displacements_m = u[0::2]
    reaction_force_n = float(-(K[0, :] @ u))
    reaction_moment_n_m = float(-(K[1, :] @ u))
    max_bending_moment_n_m = abs(transverse_load_n) * length_m

    return {
        "mode": "cantilever_beam",
        "transverse_displacements_m": transverse_displacements_m,
        "reaction_force_n": reaction_force_n,
        "reaction_moment_n_m": reaction_moment_n_m,
        "max_bending_moment_n_m": max_bending_moment_n_m,
        "residual": residual,
    }


class StructuralLinearSolver(EngineeringSolver):
    """Unified-architecture (Phase C2) real 1D linear-elastic FEA: an axial
    bar/truss element chain for `axial_load_n` requests, or an
    Euler-Bernoulli cantilever beam element chain for `transverse_load_n`
    requests. Registered as `structural_linear_1d_v1`. Replaces the old
    scalar `stress = load/area` placeholder above with an actual matrix
    assembly + direct linear solve."""

    solver_id = "structural_linear_1d_v1"
    numerical_method = "Direct linear finite-element solve using 1D bar or Euler-Bernoulli beam elements"

    @property
    def capability_metadata(self) -> CapabilityEntry:
        return get_solver_metadata(self.solver_id)

    def validate_geometry(self, request: SimulationCreateRequest) -> None:
        geometry = request.geometry
        if geometry.dimension not in self.capability_metadata.supported_dimensions:
            raise SolverValidationError(
                f"structural_linear_1d_v1 does not support geometry.dimension='{geometry.dimension}'"
            )
        if geometry.length_m is None:
            raise SolverValidationError("structural solve requires geometry.length_m")
        if geometry.cross_section_area_m2 is None:
            raise SolverValidationError("structural solve requires geometry.cross_section_area_m2")
        num_elements = geometry.num_elements or 10
        if not (1 <= num_elements <= 500):
            raise SolverValidationError("geometry.num_elements out of supported range (1-500)")

    def validate_material(self, request: SimulationCreateRequest) -> dict[str, Any]:
        try:
            elastic_modulus = materials.get_property(request.material.name, "elastic_modulus")
        except (materials.MaterialNotFoundError, materials.MaterialPropertyNotFoundError) as exc:
            raise SolverValidationError(str(exc)) from exc

        strength = None
        for prop_name in ("yield_strength", "compressive_strength"):
            try:
                strength = materials.get_property(request.material.name, prop_name)
                break
            except materials.MaterialPropertyNotFoundError:
                continue
        return {"elastic_modulus_pa": elastic_modulus.value, "strength": strength}

    def validate_boundary_conditions(self, request: SimulationCreateRequest) -> None:
        bc = request.boundary_conditions
        has_axial = bc.axial_load_n is not None
        has_transverse = bc.transverse_load_n is not None
        if has_axial == has_transverse:
            raise SolverValidationError(
                "structural solve requires exactly one of boundary_conditions.axial_load_n "
                "(bar mode) or boundary_conditions.transverse_load_n (cantilever beam mode)"
            )
        if has_transverse and request.geometry.moment_of_inertia_m4 is None:
            raise SolverValidationError(
                "cantilever beam mode requires geometry.moment_of_inertia_m4"
            )

    def prepare_model(self, request: SimulationCreateRequest, material_properties: dict[str, Any]) -> dict[str, Any]:
        return {
            "geometry": request.geometry,
            "boundary_conditions": request.boundary_conditions,
            "elastic_modulus_pa": material_properties["elastic_modulus_pa"],
            "strength": material_properties["strength"],
        }

    def generate_or_import_mesh(self, request: SimulationCreateRequest, model: dict[str, Any]) -> None:
        # The 1d element chain is generated directly inside `solve()`.
        return None

    def solve(self, request: SimulationCreateRequest, model: dict[str, Any], mesh: None) -> dict[str, Any]:
        geometry = model["geometry"]
        bc = model["boundary_conditions"]
        num_elements = geometry.num_elements or 10

        if bc.axial_load_n is not None:
            raw = _solve_axial_bar(
                num_elements=num_elements,
                length_m=geometry.length_m,
                area_m2=geometry.cross_section_area_m2,
                elastic_modulus_pa=model["elastic_modulus_pa"],
                axial_load_n=bc.axial_load_n,
            )
        else:
            raw = _solve_cantilever_beam(
                num_elements=num_elements,
                length_m=geometry.length_m,
                moment_of_inertia_m4=geometry.moment_of_inertia_m4,
                elastic_modulus_pa=model["elastic_modulus_pa"],
                transverse_load_n=bc.transverse_load_n,
            )
        raw["strength"] = model["strength"]
        return raw

    def calculate_residual(self, raw_result: dict[str, Any]) -> float | None:
        return raw_result["residual"]

    def check_convergence(self, raw_result: dict[str, Any]) -> ConvergenceStatus:
        return ConvergenceStatus(converged=True, iterations=1, residual=raw_result["residual"], tolerance=1e-6)

    def extract_metrics(self, raw_result: dict[str, Any]) -> tuple[dict[str, float], list[float], list[int]]:
        strength = raw_result["strength"]
        if raw_result["mode"] == "axial_bar":
            displacements = raw_result["displacements_m"]
            stresses = raw_result["element_stress_pa"]
            max_stress_pa = float(np.max(np.abs(stresses)))
            summary_metrics = {
                "max_displacement_m": float(np.max(np.abs(displacements))),
                "max_stress_pa": max_stress_pa,
                "max_strain": max_stress_pa / raw_result["elastic_modulus_pa"],
                "reaction_force_n": raw_result["reaction_force_n"],
            }
            if strength is not None:
                summary_metrics["factor_of_safety"] = float(strength.value / max(max_stress_pa, 1e-9))
            field_values = displacements.tolist()
            hotspot_count = min(5, len(stresses))
            hotspot_node_ids = np.argsort(np.abs(stresses))[-hotspot_count:].tolist() if hotspot_count else []
            return summary_metrics, field_values, hotspot_node_ids

        displacements = raw_result["transverse_displacements_m"]
        summary_metrics = {
            "max_displacement_m": float(np.max(np.abs(displacements))),
            "max_bending_moment_n_m": raw_result["max_bending_moment_n_m"],
            "reaction_force_n": raw_result["reaction_force_n"],
            "reaction_moment_n_m": raw_result["reaction_moment_n_m"],
        }
        field_values = displacements.tolist()
        hotspot_count = min(5, len(displacements))
        hotspot_node_ids = (
            np.argsort(np.abs(displacements))[-hotspot_count:].tolist() if hotspot_count else []
        )
        return summary_metrics, field_values, hotspot_node_ids

    def return_assumptions(self) -> list[str]:
        return [
            "Linear-elastic material behavior only (no plasticity, buckling, or large deflection).",
            "Single prismatic 1D bar/beam, fixed support at x=0, load applied only at the free end.",
        ]

    def extract_field_outputs(self, raw_result, request):
        length = request.geometry.length_m
        if raw_result["mode"] == "axial_bar":
            displacement = np.asarray(raw_result["displacements_m"], dtype=float)
            stress = np.asarray(raw_result["element_stress_pa"], dtype=float)
            return [
                NumericalFieldOutput(
                    variable_name="axial_displacement", unit="m", values=displacement,
                    axes=[{"name": "x", "unit": "m", "values": np.linspace(0, length, displacement.size).tolist()}],
                    grid_metadata={"element_type": "linear_bar", "location": "nodes"},
                ),
                NumericalFieldOutput(
                    variable_name="axial_stress", unit="Pa", values=stress,
                    axes=[{"name": "x", "unit": "m", "values": ((np.arange(stress.size) + 0.5) * length / stress.size).tolist()}],
                    grid_metadata={"element_type": "linear_bar", "location": "element_centers"},
                ),
            ]
        displacement = np.asarray(raw_result["transverse_displacements_m"], dtype=float)
        return [NumericalFieldOutput(
            variable_name="transverse_displacement", unit="m", values=displacement,
            axes=[{"name": "x", "unit": "m", "values": np.linspace(0, length, displacement.size).tolist()}],
            grid_metadata={"element_type": "euler_bernoulli_beam", "location": "nodes"},
        )]
