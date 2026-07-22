"""Two-dimensional rectangular-grid electrostatic Laplace/Poisson solver."""
from __future__ import annotations

from typing import Any

import numpy as np

from app.module2_simulation import materials
from app.module2_simulation.schemas import CapabilityEntry, ConvergenceStatus, SimulationCreateRequest
from app.module2_simulation.solver_registry import get_solver_metadata
from app.module2_simulation.solvers.base_solver import EngineeringSolver, NumericalFieldOutput, SolverValidationError


class ElectrostaticRectangularSolver(EngineeringSolver):
    solver_id = "electrostatic_rectangular_2d_v1"
    numerical_method = "Five-point finite-difference Poisson solve with Gauss-Seidel iteration"

    @property
    def capability_metadata(self) -> CapabilityEntry:
        return get_solver_metadata(self.solver_id)

    def validate_geometry(self, request):
        g = request.geometry
        if g.dimension != "2d" or g.width_m is None or g.height_m is None:
            raise SolverValidationError("electrostatic solver requires 2d width_m and height_m")
        nx, ny = g.grid_resolution or 21, g.grid_resolution_y or g.grid_resolution or 21
        if not (5 <= nx <= 60 and 5 <= ny <= 60):
            raise SolverValidationError("electrostatic grid dimensions must be 5-60")

    def validate_material(self, request) -> dict[str, Any]:
        try:
            return {"permittivity_f_m": materials.get_property(request.material.name, "permittivity").value}
        except (materials.MaterialNotFoundError, materials.MaterialPropertyNotFoundError) as exc:
            raise SolverValidationError(str(exc)) from exc

    def validate_boundary_conditions(self, request):
        bc = request.boundary_conditions
        if bc.potential_gradient_x_v_m is not None and bc.potential_left_v is not None:
            return
        values = (bc.potential_left_v, bc.potential_right_v, bc.potential_top_v, bc.potential_bottom_v)
        if any(value is None for value in values):
            raise SolverValidationError("fixed potential is required on all four rectangular boundaries")
        if bc.charge_density_c_m3 is not None and abs(bc.charge_density_c_m3) > 1e-3:
            raise SolverValidationError("charge_density_c_m3 exceeds bounded magnitude 1e-3")

    def prepare_model(self, request, material_properties):
        return {"request": request, **material_properties}

    def generate_or_import_mesh(self, request, model):
        g = request.geometry
        nx, ny = g.grid_resolution or 21, g.grid_resolution_y or g.grid_resolution or 21
        return np.linspace(0, g.width_m, nx), np.linspace(0, g.height_m, ny)

    def solve(self, request, model, mesh):
        x, y = mesh
        nx, ny = x.size, y.size
        dx, dy = float(x[1]-x[0]), float(y[1]-y[0])
        bc, settings = request.boundary_conditions, request.numerical_settings
        potential = np.zeros((ny, nx), dtype=float)
        if bc.potential_gradient_x_v_m is not None:
            profile = bc.potential_left_v + bc.potential_gradient_x_v_m * x
            potential[:, 0], potential[:, -1] = profile[0], profile[-1]
            potential[0, :], potential[-1, :] = profile, profile
        else:
            potential[:, 0], potential[:, -1] = bc.potential_left_v, bc.potential_right_v
            potential[0, :], potential[-1, :] = bc.potential_bottom_v, bc.potential_top_v
            # Corners are the arithmetic mean of the two prescribed meeting faces.
            potential[0, 0] = (bc.potential_left_v + bc.potential_bottom_v) / 2
            potential[-1, 0] = (bc.potential_left_v + bc.potential_top_v) / 2
            potential[0, -1] = (bc.potential_right_v + bc.potential_bottom_v) / 2
            potential[-1, -1] = (bc.potential_right_v + bc.potential_top_v) / 2
        rho = bc.charge_density_c_m3 or 0.0
        denominator = 2.0 / dx**2 + 2.0 / dy**2
        history = []
        for iteration in range(1, settings.max_iterations + 1):
            max_delta = 0.0
            for j in range(1, ny-1):
                for i in range(1, nx-1):
                    old = potential[j, i]
                    potential[j, i] = ((potential[j, i-1]+potential[j, i+1])/dx**2
                                       +(potential[j-1, i]+potential[j+1, i])/dy**2
                                       +rho/model["permittivity_f_m"]) / denominator
                    max_delta = max(max_delta, abs(potential[j, i]-old))
            history.append(max_delta)
            if max_delta <= settings.tolerance:
                break
        edge_order = 2 if min(nx, ny) >= 3 else 1
        d_v_dy, d_v_dx = np.gradient(potential, y, x, edge_order=edge_order)
        return {"potential": potential, "electric_x": -d_v_dx, "electric_y": -d_v_dy,
                "x": x, "y": y, "history": history, "iterations": iteration,
                "tolerance": settings.tolerance}

    def calculate_residual(self, raw_result):
        return float(raw_result["history"][-1])

    def check_convergence(self, raw_result):
        residual = self.calculate_residual(raw_result)
        return ConvergenceStatus(converged=residual <= raw_result["tolerance"], iterations=raw_result["iterations"],
                                 residual=residual, tolerance=raw_result["tolerance"])

    def extract_metrics(self, raw_result):
        potential = raw_result["potential"]
        magnitude = np.hypot(raw_result["electric_x"], raw_result["electric_y"])
        return ({"min_potential_v": float(potential.min()), "max_potential_v": float(potential.max()),
                 "max_electric_field_v_m": float(magnitude.max()),
                 "final_iteration_delta_v": self.calculate_residual(raw_result)},
                potential.ravel().tolist(), [int(np.argmax(magnitude))])

    def extract_field_outputs(self, raw_result, request):
        axes = [{"name":"y","unit":"m","values":raw_result["y"].tolist()},
                {"name":"x","unit":"m","values":raw_result["x"].tolist()}]
        meta = {"dimension":"2d", "structured":True, "residual_history":raw_result["history"]}
        ex, ey = raw_result["electric_x"], raw_result["electric_y"]
        return [NumericalFieldOutput(variable_name="electric_potential", unit="V", values=raw_result["potential"], axes=axes, grid_metadata=meta),
                NumericalFieldOutput(variable_name="electric_field_x", unit="V/m", values=ex, axes=axes, grid_metadata=meta),
                NumericalFieldOutput(variable_name="electric_field_y", unit="V/m", values=ey, axes=axes, grid_metadata=meta),
                NumericalFieldOutput(variable_name="electric_field_magnitude", unit="V/m", values=np.hypot(ex,ey), axes=axes, grid_metadata=meta)]

    def return_assumptions(self):
        return ["Two-dimensional electrostatics on a uniform rectangular grid.",
                "Scalar constant permittivity and fixed-potential boundaries.",
                "This is not an electromagnetic-wave or full Maxwell solver."]

    def return_warnings(self):
        return ["Corner potentials are averaged where adjacent prescribed faces differ."]
