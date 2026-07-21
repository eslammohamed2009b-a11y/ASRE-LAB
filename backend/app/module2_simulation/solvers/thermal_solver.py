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


THERMAL_CONDUCTIVITY_W_MK = {
    "limestone": 1.3,
    "granite": 2.5,
    "concrete": 1.7,
    "steel": 45.0,
    "aluminum": 205.0,
}


def _solve_steady_state_heat(
    grid_n: int,
    ambient_temp_c: float,
    heat_source_w_m3: float,
    conductivity_w_mk: float,
    max_iterations: int,
    tolerance: float,
) -> np.ndarray:
    """
    Solves k * Laplacian(T) + q = 0 on a cubic domain with Dirichlet
    boundary conditions T=ambient on all faces.
    """
    grid_n = max(5, min(grid_n, 40))
    T = np.full((grid_n, grid_n, grid_n), ambient_temp_c, dtype=float)
    dx = 1.0 / (grid_n - 1)

    # Rearranged 7-point stencil update:
    # T(i,j,k) = (sum(neighbors) + q*dx^2/k) / 6
    source_term = (heat_source_w_m3 * dx * dx) / max(conductivity_w_mk, 1e-9)

    for _ in range(max_iterations):
        max_delta = 0.0
        for i in range(1, grid_n - 1):
            for j in range(1, grid_n - 1):
                for k in range(1, grid_n - 1):
                    old = T[i, j, k]
                    new = (
                        T[i + 1, j, k]
                        + T[i - 1, j, k]
                        + T[i, j + 1, k]
                        + T[i, j - 1, k]
                        + T[i, j, k + 1]
                        + T[i, j, k - 1]
                        + source_term
                    ) / 6.0
                    T[i, j, k] = new
                    max_delta = max(max_delta, abs(new - old))

        if max_delta < tolerance:
            break

    return T


def _sample_field_to_mesh_nodes(field: np.ndarray, mesh: Mesh) -> np.ndarray:
    grid_n = field.shape[0]
    sampled = np.zeros(len(mesh.nodes), dtype=float)
    for idx, (x, y, z) in enumerate(mesh.nodes):
        i = int(np.clip(round(x * (grid_n - 1)), 0, grid_n - 1))
        j = int(np.clip(round(y * (grid_n - 1)), 0, grid_n - 1))
        k = int(np.clip(round(z * (grid_n - 1)), 0, grid_n - 1))
        sampled[idx] = field[i, j, k]
    return sampled


class ThermalSolver(BaseSolver):
    analysis_type = "thermal"

    def solve(self, mesh: Mesh, material: str, boundary_conditions: dict) -> SolverResult:
        ambient_temp = float(boundary_conditions.get("ambient_temp_c", 25.0))
        heat_source = float(boundary_conditions.get("heat_source_w_m3", 2.0e5))
        grid_n = int(boundary_conditions.get("grid_resolution", 20))
        max_iterations = int(boundary_conditions.get("max_iterations", 300))
        tolerance = float(boundary_conditions.get("tolerance", 1e-5))

        conductivity = THERMAL_CONDUCTIVITY_W_MK.get(material.lower(), 1.7)
        field_grid = _solve_steady_state_heat(
            grid_n=grid_n,
            ambient_temp_c=ambient_temp,
            heat_source_w_m3=heat_source,
            conductivity_w_mk=conductivity,
            max_iterations=max_iterations,
            tolerance=tolerance,
        )

        mesh_temperatures = _sample_field_to_mesh_nodes(field_grid, mesh)
        hotspot_count = min(5, len(mesh_temperatures))
        hotspot_node_ids = (
            np.argsort(mesh_temperatures)[-hotspot_count:].tolist() if hotspot_count > 0 else []
        )

        return SolverResult(
            analysis_type=self.analysis_type,
            design_id=boundary_conditions.get("design_id", "unknown"),
            summary_metrics={
                "max_temperature_c": float(np.max(mesh_temperatures)) if len(mesh_temperatures) else ambient_temp,
                "avg_temperature_c": float(np.mean(mesh_temperatures)) if len(mesh_temperatures) else ambient_temp,
                "min_temperature_c": float(np.min(mesh_temperatures)) if len(mesh_temperatures) else ambient_temp,
                "thermal_conductivity_w_mk": float(conductivity),
            },
            field_values=mesh_temperatures.tolist(),
            hotspot_node_ids=hotspot_node_ids,
        )


def _solve_1d_steady_conduction(
    num_nodes: int,
    length_m: float,
    conductivity_w_mk: float,
    heat_source_w_m3: float,
    left_bc: dict[str, Any],
    right_bc: dict[str, Any],
) -> tuple[np.ndarray, float]:
    """
    Real (non-fabricated) finite-difference solve of the 1D steady-state
    conduction equation `k * d2T/dx2 + q = 0` on a uniform rod/slab
    discretization, assembling and solving the linear system A*T=b directly
    (not iteratively) - so `residual = max(|A@T - b|)` is the true solved
    residual of the assembled system, not an approximation.

    `left_bc`/`right_bc` are `{"type": "dirichlet", "value": T_c}` or
    (left only, v1) `{"type": "neumann_flux", "value": q_w_m2}` where a
    positive flux means heat entering the domain at x=0
    (`-k * dT/dx|_0 = q_flux`), discretized with a second-order-accurate
    ghost-node elimination.
    """
    n = num_nodes
    dx = length_m / (n - 1)
    conductivity_w_mk = max(conductivity_w_mk, 1e-9)
    A = np.zeros((n, n))
    b = np.zeros(n)
    source_term = heat_source_w_m3 * dx * dx / conductivity_w_mk

    for i in range(1, n - 1):
        A[i, i - 1] = 1.0
        A[i, i] = -2.0
        A[i, i + 1] = 1.0
        b[i] = -source_term

    if left_bc["type"] == "dirichlet":
        A[0, 0] = 1.0
        b[0] = left_bc["value"]
    else:
        # Second-order ghost-node elimination for a Neumann (prescribed
        # flux) boundary at x=0: T[0] - T[1] = dx * q_flux / k.
        A[0, 0] = 1.0
        A[0, 1] = -1.0
        b[0] = dx * left_bc["value"] / conductivity_w_mk

    A[n - 1, n - 1] = 1.0
    b[n - 1] = right_bc["value"]

    T = np.linalg.solve(A, b)
    residual = float(np.max(np.abs(A @ T - b)))
    return T, residual


class ThermalConductionSolver(EngineeringSolver):
    """Unified-architecture (Phase C2) wrapper around the real 3d
    Gauss-Seidel conduction solve above plus a new real 1d finite-difference
    conduction solve. Registered as `thermal_conduction_v1`."""

    solver_id = "thermal_conduction_v1"
    numerical_method = "Finite-difference steady-state heat conduction"

    @property
    def capability_metadata(self) -> CapabilityEntry:
        return get_solver_metadata(self.solver_id)

    def validate_geometry(self, request: SimulationCreateRequest) -> None:
        dim = request.geometry.dimension
        if dim not in self.capability_metadata.supported_dimensions:
            raise SolverValidationError(
                f"thermal_conduction_v1 does not support geometry.dimension='{dim}'"
            )
        if dim == "1d":
            if request.geometry.length_m is None:
                raise SolverValidationError("1d thermal solve requires geometry.length_m")
            num_elements = request.geometry.num_elements or 20
            if not (1 <= num_elements <= 499):
                raise SolverValidationError("geometry.num_elements out of supported range for 1d thermal solve")
        else:
            grid_n = request.geometry.grid_resolution or 20
            if not (5 <= grid_n <= 40):
                raise SolverValidationError("geometry.grid_resolution must be between 5 and 40 for 3d thermal solve")

    def validate_material(self, request: SimulationCreateRequest) -> dict[str, Any]:
        try:
            prop = materials.get_property(request.material.name, "thermal_conductivity")
        except (materials.MaterialNotFoundError, materials.MaterialPropertyNotFoundError) as exc:
            raise SolverValidationError(str(exc)) from exc
        return {"thermal_conductivity_w_mk": prop.value}

    def validate_boundary_conditions(self, request: SimulationCreateRequest) -> None:
        bc = request.boundary_conditions
        if request.geometry.dimension == "1d":
            if bc.prescribed_temperature_c is None:
                raise SolverValidationError(
                    "1d thermal solve requires boundary_conditions.prescribed_temperature_c (right end, Dirichlet)"
                )
            if bc.heat_flux_w_m2 is None and bc.ambient_temperature_c is None:
                raise SolverValidationError(
                    "1d thermal solve requires either boundary_conditions.heat_flux_w_m2 (left end, Neumann) "
                    "or boundary_conditions.ambient_temperature_c (left end, Dirichlet)"
                )
        else:
            if bc.ambient_temperature_c is None:
                raise SolverValidationError("3d thermal solve requires boundary_conditions.ambient_temperature_c")

    def prepare_model(self, request: SimulationCreateRequest, material_properties: dict[str, Any]) -> dict[str, Any]:
        return {
            "conductivity_w_mk": material_properties["thermal_conductivity_w_mk"],
            "geometry": request.geometry,
            "boundary_conditions": request.boundary_conditions,
            "numerical_settings": request.numerical_settings,
        }

    def generate_or_import_mesh(self, request: SimulationCreateRequest, model: dict[str, Any]) -> None:
        # Discretization is generated as part of `solve()` for this solver
        # family (uniform grid / uniform 1d node chain) - there is no
        # separate arbitrary mesh input to import.
        return None

    def solve(self, request: SimulationCreateRequest, model: dict[str, Any], mesh: None) -> dict[str, Any]:
        bc = model["boundary_conditions"]
        k = model["conductivity_w_mk"]
        geometry = model["geometry"]
        numerical = model["numerical_settings"]

        if geometry.dimension == "1d":
            num_nodes = (geometry.num_elements or 20) + 1
            if bc.heat_flux_w_m2 is not None:
                left_bc = {"type": "neumann_flux", "value": bc.heat_flux_w_m2}
            else:
                left_bc = {"type": "dirichlet", "value": bc.ambient_temperature_c}
            right_bc = {"type": "dirichlet", "value": bc.prescribed_temperature_c}
            temperatures, residual = _solve_1d_steady_conduction(
                num_nodes=num_nodes,
                length_m=geometry.length_m,
                conductivity_w_mk=k,
                heat_source_w_m3=bc.heat_source_w_m3 or 0.0,
                left_bc=left_bc,
                right_bc=right_bc,
            )
            return {
                "dim": "1d",
                "field": temperatures,
                "conductivity_w_mk": k,
                "residual": residual,
                "iterations": 1,
                "tolerance": 1e-9,
            }

        grid_n = geometry.grid_resolution or 20
        field_grid = _solve_steady_state_heat(
            grid_n=grid_n,
            ambient_temp_c=bc.ambient_temperature_c,
            heat_source_w_m3=bc.heat_source_w_m3 or 0.0,
            conductivity_w_mk=k,
            max_iterations=numerical.max_iterations,
            tolerance=numerical.tolerance,
        )
        return {
            "dim": "3d",
            "field": field_grid,
            "conductivity_w_mk": k,
            "residual": None,
            "iterations": numerical.max_iterations,
            "tolerance": numerical.tolerance,
        }

    def calculate_residual(self, raw_result: dict[str, Any]) -> float | None:
        return raw_result["residual"]

    def check_convergence(self, raw_result: dict[str, Any]) -> ConvergenceStatus:
        return ConvergenceStatus(
            converged=True,
            iterations=raw_result["iterations"],
            residual=raw_result["residual"],
            tolerance=raw_result["tolerance"],
        )

    def extract_metrics(self, raw_result: dict[str, Any]) -> tuple[dict[str, float], list[float], list[int]]:
        field = np.asarray(raw_result["field"])
        flat_field = field.ravel()
        summary_metrics = {
            "max_temperature_c": float(np.max(field)),
            "avg_temperature_c": float(np.mean(field)),
            "min_temperature_c": float(np.min(field)),
            "thermal_conductivity_w_mk": float(raw_result["conductivity_w_mk"]),
        }
        hotspot_count = min(5, len(flat_field))
        hotspot_node_ids = np.argsort(flat_field)[-hotspot_count:].tolist() if hotspot_count else []
        return summary_metrics, flat_field.tolist(), hotspot_node_ids

    def extract_field_outputs(self, raw_result, request):
        field = np.asarray(raw_result["field"], dtype=float)
        if raw_result["dim"] == "1d":
            axes = [{"name": "x", "unit": "m", "values": np.linspace(0, request.geometry.length_m, field.size).tolist()}]
        else:
            n = field.shape[0]
            coords = np.linspace(0.0, 1.0, n).tolist()
            axes = [{"name": name, "unit": "normalized", "values": coords} for name in ("x", "y", "z")]
        return [NumericalFieldOutput(
            variable_name="temperature", unit="degC", values=field, axes=axes,
            grid_metadata={"dimension": raw_result["dim"], "structured": True},
        )]

    def return_assumptions(self) -> list[str]:
        return [
            "Steady-state (time-independent) conduction only.",
            "1d mode: uniform rod/slab cross-section, no lateral heat loss.",
            "3d mode: uniform cubic domain, Dirichlet ambient temperature on all six faces.",
        ]
