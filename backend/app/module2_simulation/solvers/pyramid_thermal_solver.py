"""Bounded geometry-aware thermal conduction for a solid square pyramid.

This is a structured-grid finite-difference model over a staircase mask. It
does not consume CAD meshes and is not a general 3D FEA solver.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from app.module2_simulation import materials
from app.module2_simulation.schemas import CapabilityEntry, ConvergenceStatus, SimulationCreateRequest
from app.module2_simulation.solver_registry import get_solver_metadata
from app.module2_simulation.solvers.base_solver import EngineeringSolver, NumericalFieldOutput, SolverValidationError


def solve_pyramid_conduction(
    *, base_length_m: float, height_m: float, grid_resolution: int,
    conductivity_w_mk: float, ambient_temperature_c: float,
    base_temperature_c: float, heat_source_w_m3: float,
    max_iterations: int, tolerance: float,
) -> dict[str, Any]:
    """Solve ``k*Laplacian(T) + q = 0`` inside a square-pyramid mask."""
    n = grid_resolution
    x = np.linspace(-base_length_m / 2.0, base_length_m / 2.0, n)
    y = x.copy()
    z = np.linspace(0.0, height_m, n)
    xx, yy, zz = np.meshgrid(x, y, z, indexing="ij")
    half_width = (base_length_m / 2.0) * np.maximum(0.0, 1.0 - zz / height_m)
    mask = (np.abs(xx) <= half_width + 1e-12) & (np.abs(yy) <= half_width + 1e-12)

    # A masked point is an exposed staircase boundary when any direct
    # neighbour lies outside the pyramid. The base temperature overrides the
    # ambient side-surface temperature where the two meet.
    boundary = np.zeros_like(mask)
    for axis in range(3):
        boundary |= mask & ~np.roll(mask, 1, axis=axis)
        boundary |= mask & ~np.roll(mask, -1, axis=axis)
    boundary[0, :, :] |= mask[0, :, :]
    boundary[-1, :, :] |= mask[-1, :, :]
    boundary[:, 0, :] |= mask[:, 0, :]
    boundary[:, -1, :] |= mask[:, -1, :]
    boundary[:, :, 0] |= mask[:, :, 0]
    boundary[:, :, -1] |= mask[:, :, -1]
    interior = mask & ~boundary

    temperature = np.full((n, n, n), ambient_temperature_c, dtype=float)
    temperature[:, :, 0][mask[:, :, 0]] = base_temperature_c
    dx = base_length_m / (n - 1)
    dz = height_m / (n - 1)
    horizontal_coefficient = 1.0 / (dx * dx)
    vertical_coefficient = 1.0 / (dz * dz)
    denominator = 4.0 * horizontal_coefficient + 2.0 * vertical_coefficient
    source = heat_source_w_m3 / conductivity_w_mk

    residual_history: list[float] = []
    interior_indices = np.argwhere(interior)
    for _ in range(max_iterations):
        max_update = 0.0
        for i, j, k_index in interior_indices:
            previous = temperature[i, j, k_index]
            updated = (
                horizontal_coefficient * (
                    temperature[i - 1, j, k_index] + temperature[i + 1, j, k_index]
                    + temperature[i, j - 1, k_index] + temperature[i, j + 1, k_index]
                )
                + vertical_coefficient * (
                    temperature[i, j, k_index - 1] + temperature[i, j, k_index + 1]
                )
                + source
            ) / denominator
            temperature[i, j, k_index] = updated
            max_update = max(max_update, abs(updated - previous))
        residual_history.append(float(max_update))
        if max_update <= tolerance:
            break

    active_values = temperature[mask]
    maximum_gradient = 0.0
    for axis, spacing in ((0, dx), (1, dx), (2, dz)):
        neighbour_pair = mask & np.roll(mask, -1, axis=axis)
        differences = np.abs(temperature - np.roll(temperature, -1, axis=axis))
        if np.any(neighbour_pair):
            maximum_gradient = max(
                maximum_gradient, float(np.max(differences[neighbour_pair]) / spacing)
            )

    cell_volume = dx * dx * dz
    return {
        "temperature": temperature, "mask": mask, "x": x, "y": y, "z": z,
        "active_values": active_values, "active_cell_count": int(mask.sum()),
        "estimated_domain_volume_m3": float(mask.sum() * cell_volume),
        "integrated_heat_source_w": float(heat_source_w_m3 * mask.sum() * cell_volume),
        "maximum_gradient_k_m": maximum_gradient,
        "iterations": len(residual_history),
        "residual": residual_history[-1] if residual_history else 0.0,
        "residual_history": residual_history, "tolerance": tolerance,
    }


class PyramidThermalConductionSolver(EngineeringSolver):
    solver_id = "pyramid_thermal_conduction_v1"
    numerical_method = "Masked Cartesian-grid finite-difference Gauss-Seidel conduction"

    @property
    def capability_metadata(self) -> CapabilityEntry:
        return get_solver_metadata(self.solver_id)

    def validate_geometry(self, request: SimulationCreateRequest) -> None:
        geometry = request.geometry
        if geometry.dimension != "pyramid3d":
            raise SolverValidationError("pyramid thermal conduction requires geometry.dimension='pyramid3d'")
        if geometry.base_length_m is None or geometry.height_m is None:
            raise SolverValidationError("pyramid3d requires geometry.base_length_m and geometry.height_m")
        ratio = geometry.height_m / geometry.base_length_m
        if not 0.1 <= ratio <= 10.0:
            raise SolverValidationError("pyramid height/base ratio must be between 0.1 and 10")
        resolution = geometry.grid_resolution or 17
        if not 9 <= resolution <= 41 or resolution % 2 == 0:
            raise SolverValidationError("pyramid grid_resolution must be an odd integer from 9 through 41")

    def validate_material(self, request: SimulationCreateRequest) -> dict[str, Any]:
        try:
            prop = materials.get_property(request.material.name, "thermal_conductivity")
        except (materials.MaterialNotFoundError, materials.MaterialPropertyNotFoundError) as exc:
            raise SolverValidationError(str(exc)) from exc
        if prop.value <= 0:
            raise SolverValidationError("thermal conductivity must be positive")
        return {"conductivity_w_mk": prop.value}

    def validate_boundary_conditions(self, request: SimulationCreateRequest) -> None:
        bc = request.boundary_conditions
        if bc.ambient_temperature_c is None or bc.prescribed_temperature_c is None:
            raise SolverValidationError(
                "pyramid thermal conduction requires ambient_temperature_c (sides) and "
                "prescribed_temperature_c (base)"
            )
        if bc.heat_source_w_m3 is None:
            raise SolverValidationError("pyramid thermal conduction requires heat_source_w_m3")

    def prepare_model(self, request: SimulationCreateRequest, material_properties: dict[str, Any]) -> dict[str, Any]:
        return {"request": request, **material_properties}

    def generate_or_import_mesh(self, request: SimulationCreateRequest, model: dict[str, Any]) -> None:
        return None

    def solve(self, request: SimulationCreateRequest, model: dict[str, Any], mesh: None) -> dict[str, Any]:
        geometry, bc = request.geometry, request.boundary_conditions
        result = solve_pyramid_conduction(
            base_length_m=geometry.base_length_m,
            height_m=geometry.height_m,
            grid_resolution=geometry.grid_resolution or 17,
            conductivity_w_mk=model["conductivity_w_mk"],
            ambient_temperature_c=bc.ambient_temperature_c,
            base_temperature_c=bc.prescribed_temperature_c,
            heat_source_w_m3=bc.heat_source_w_m3,
            max_iterations=request.numerical_settings.max_iterations,
            tolerance=request.numerical_settings.tolerance,
        )
        benchmark = solve_pyramid_conduction(
            base_length_m=geometry.base_length_m,
            height_m=geometry.height_m,
            grid_resolution=geometry.grid_resolution or 17,
            conductivity_w_mk=model["conductivity_w_mk"],
            ambient_temperature_c=bc.ambient_temperature_c,
            base_temperature_c=bc.ambient_temperature_c,
            heat_source_w_m3=0.0,
            max_iterations=2,
            tolerance=1e-12,
        )
        computed = float(np.max(benchmark["active_values"]))
        result["benchmark"] = {
            "id": "pyramid_constant_dirichlet",
            "reference_type": "analytical constant-temperature solution",
            "reference_result_c": float(bc.ambient_temperature_c),
            "computed_result_c": computed,
            "absolute_error_c": abs(computed - float(bc.ambient_temperature_c)),
            "declared_tolerance_c": 1e-10,
            "passed": abs(computed - float(bc.ambient_temperature_c)) <= 1e-10,
            "assumptions": "Zero volumetric source and equal Dirichlet temperature on every boundary.",
        }
        return result

    def calculate_residual(self, raw_result: dict[str, Any]) -> float:
        return raw_result["residual"]

    def check_convergence(self, raw_result: dict[str, Any]) -> ConvergenceStatus:
        return ConvergenceStatus(
            converged=raw_result["residual"] <= raw_result["tolerance"],
            iterations=raw_result["iterations"], residual=raw_result["residual"],
            tolerance=raw_result["tolerance"],
        )

    def extract_metrics(self, raw_result: dict[str, Any]) -> tuple[dict[str, float], list[float], list[int]]:
        values = raw_result["active_values"]
        hottest = np.argsort(values)[-min(5, values.size):].tolist()
        return ({
            "max_temperature_c": float(np.max(values)),
            "avg_temperature_c": float(np.mean(values)),
            "min_temperature_c": float(np.min(values)),
            "max_temperature_gradient_k_m": raw_result["maximum_gradient_k_m"],
            "estimated_domain_volume_m3": raw_result["estimated_domain_volume_m3"],
            "integrated_heat_source_w": raw_result["integrated_heat_source_w"],
            "active_grid_cell_count": float(raw_result["active_cell_count"]),
        }, values.tolist(), hottest)

    def serialize_results(self, raw_result: dict[str, Any], convergence: ConvergenceStatus):
        payload = super().serialize_results(raw_result, convergence)
        payload.residual_history = raw_result["residual_history"][-5000:]
        return payload

    def extract_field_outputs(self, raw_result: dict[str, Any], request: SimulationCreateRequest):
        axes = [
            {"name": "x", "unit": "m", "values": raw_result["x"].tolist()},
            {"name": "y", "unit": "m", "values": raw_result["y"].tolist()},
            {"name": "z", "unit": "m", "values": raw_result["z"].tolist()},
        ]
        return [
            NumericalFieldOutput(
                variable_name="temperature", unit="degC", values=raw_result["temperature"], axes=axes,
                grid_metadata={
                    "dimension": "pyramid3d", "structured": True,
                    "physical_values_require_domain_mask": True,
                    "mask_variable": "pyramid_domain_mask",
                },
            ),
            NumericalFieldOutput(
                variable_name="pyramid_domain_mask", unit="1",
                values=raw_result["mask"].astype(float), axes=axes,
                grid_metadata={"dimension": "pyramid3d", "structured": True, "mask": True},
            ),
        ]

    def additional_validation_metadata(self, raw_result: dict[str, Any], request: SimulationCreateRequest):
        return {
            "benchmark": raw_result["benchmark"],
            "convergence_evidence": {
                "current_grid_resolution": request.geometry.grid_resolution or 17,
                "current_iteration_converged": raw_result["residual"] <= raw_result["tolerance"],
                "resolution_refinement_performed_for_current_run": False,
                "required_action": "Run the same scenario at at least three odd grid resolutions before treating spatial convergence as established.",
            },
        }

    def return_assumptions(self) -> list[str]:
        return [
            "Steady-state, isotropic, homogeneous heat conduction in a solid square pyramid.",
            "The base is isothermal at prescribed_temperature_c.",
            "The staircase-approximated side surfaces and apex are isothermal at ambient_temperature_c.",
            "A uniform volumetric heat source is applied inside the masked domain.",
            "The CAD boundary is not meshed; a structured Cartesian mask approximates the parametric pyramid.",
        ]

    def return_warnings(self) -> list[str]:
        return [
            "Geometry uses a resolution-dependent staircase mask; inspect convergence across odd grid resolutions.",
            "Temperature values outside the domain mask are storage fill values, not physical pyramid results.",
        ]
