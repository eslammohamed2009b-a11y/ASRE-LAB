"""Bounded one-dimensional frequency-domain acoustic duct solver."""
from __future__ import annotations

from typing import Any

import numpy as np

from app.module2_simulation import materials
from app.module2_simulation.schemas import CapabilityEntry, ConvergenceStatus, SimulationCreateRequest
from app.module2_simulation.solver_registry import get_solver_metadata
from app.module2_simulation.solvers.base_solver import EngineeringSolver, NumericalFieldOutput, SolverValidationError


class AcousticDuctSolver(EngineeringSolver):
    """Second-order finite-difference Helmholtz solve on a uniform 1D duct."""

    solver_id = "acoustic_duct_1d_v1"
    numerical_method = "Second-order central finite difference Helmholtz solve"

    @property
    def capability_metadata(self) -> CapabilityEntry:
        return get_solver_metadata(self.solver_id)

    def validate_geometry(self, request: SimulationCreateRequest) -> None:
        if request.geometry.dimension != "1d" or request.geometry.length_m is None:
            raise SolverValidationError("acoustic_duct_1d_v1 requires a 1d duct with length_m")
        if not 4 <= (request.geometry.num_elements or 40) <= 500:
            raise SolverValidationError("acoustic duct requires 4-500 elements")

    def validate_material(self, request: SimulationCreateRequest) -> dict[str, Any]:
        try:
            return {
                "density_kg_m3": materials.get_property(request.material.name, "density").value,
                "speed_of_sound_m_s": materials.get_property(request.material.name, "speed_of_sound").value,
            }
        except (materials.MaterialNotFoundError, materials.MaterialPropertyNotFoundError) as exc:
            raise SolverValidationError(str(exc)) from exc

    def validate_boundary_conditions(self, request: SimulationCreateRequest) -> None:
        bc = request.boundary_conditions
        if bc.source_frequency_hz is None or bc.source_pressure_pa is None:
            raise SolverValidationError("source_frequency_hz and source_pressure_pa are required")
        if (bc.acoustic_left_boundary or "driven") != "driven":
            raise SolverValidationError("left boundary must be 'driven'")
        if (bc.acoustic_right_boundary or "pressure_release") not in {"pressure_release", "rigid"}:
            raise SolverValidationError("right boundary must be 'pressure_release' or 'rigid'")

    def prepare_model(self, request, material_properties):
        return {"request": request, **material_properties}

    def generate_or_import_mesh(self, request, model):
        return np.linspace(0.0, request.geometry.length_m, (request.geometry.num_elements or 40) + 1)

    def solve(self, request, model, mesh):
        n, dx = mesh.size, float(mesh[1] - mesh[0])
        omega = 2.0 * np.pi * request.boundary_conditions.source_frequency_hz
        wave_number = omega / model["speed_of_sound_m_s"]
        if wave_number * dx > 0.5:
            raise SolverValidationError("grid is too coarse: require k*dx <= 0.5 for bounded dispersion")
        matrix = np.zeros((n, n), dtype=complex)
        rhs = np.zeros(n, dtype=complex)
        matrix[0, 0], rhs[0] = 1.0, complex(request.boundary_conditions.source_pressure_pa)
        for index in range(1, n - 1):
            matrix[index, index - 1] = 1.0
            matrix[index, index] = -2.0 + (wave_number * dx) ** 2
            matrix[index, index + 1] = 1.0
        right = request.boundary_conditions.acoustic_right_boundary or "pressure_release"
        if right == "pressure_release":
            matrix[-1, -1] = 1.0
        else:
            matrix[-1, -1], matrix[-1, -2] = 1.0, -1.0
        try:
            pressure = np.linalg.solve(matrix, rhs)
        except np.linalg.LinAlgError as exc:
            raise SolverValidationError("frequency is too close to a discrete duct resonance") from exc
        residual = float(np.max(np.abs(matrix @ pressure - rhs)))
        fundamental = model["speed_of_sound_m_s"] / (2.0 * request.geometry.length_m)
        if right == "rigid":
            fundamental *= 0.5
        return {"x": mesh, "pressure": pressure, "residual": residual, "frequency": omega / (2*np.pi),
                "fundamental": fundamental, "wave_number": wave_number}

    def calculate_residual(self, raw_result):
        return raw_result["residual"]

    def check_convergence(self, raw_result):
        return ConvergenceStatus(converged=raw_result["residual"] <= 1e-8, iterations=1,
                                 residual=raw_result["residual"], tolerance=1e-8)

    def extract_metrics(self, raw_result):
        amplitude = np.abs(raw_result["pressure"])
        return ({"max_pressure_amplitude_pa": float(amplitude.max()),
                 "source_frequency_hz": float(raw_result["frequency"]),
                 "fundamental_resonance_hz": float(raw_result["fundamental"]),
                 "wave_number_rad_m": float(raw_result["wave_number"])},
                amplitude.tolist(), [int(np.argmax(amplitude))])

    def extract_field_outputs(self, raw_result, request):
        axis = [{"name": "x", "unit": "m", "values": raw_result["x"].tolist()}]
        pressure = raw_result["pressure"]
        common = {"dimension": "1d", "structured": True, "frequency_hz": raw_result["frequency"]}
        return [
            NumericalFieldOutput(variable_name="pressure_real", unit="Pa", values=pressure.real, axes=axis, grid_metadata=common),
            NumericalFieldOutput(variable_name="pressure_amplitude", unit="Pa", values=np.abs(pressure), axes=axis, grid_metadata=common),
            NumericalFieldOutput(variable_name="pressure_phase", unit="rad", values=np.angle(pressure), axes=axis, grid_metadata=common),
        ]

    def return_assumptions(self):
        return ["One-dimensional uniform lossless duct.", "Linear small-amplitude frequency-domain acoustics.",
                "Plane-wave propagation; no arbitrary room or CAD geometry."]

    def return_warnings(self):
        return ["No viscous, thermal, radiation, or higher-mode losses are modeled."]
