"""Steady, fully developed, incompressible laminar rectangular channel flow."""
from __future__ import annotations

from typing import Any

import numpy as np

from app.module2_simulation import materials
from app.module2_simulation.schemas import CapabilityEntry, ConvergenceStatus
from app.module2_simulation.solver_registry import get_solver_metadata
from app.module2_simulation.solvers.base_solver import EngineeringSolver, NumericalFieldOutput, SolverValidationError


class LaminarChannelFlowSolver(EngineeringSolver):
    """Finite-difference Poiseuille solve, replicated along the developed streamwise direction."""

    solver_id = "cfd_laminar_channel_2d_v1"
    numerical_method = "Second-order finite-difference fully developed Navier-Stokes reduction"

    @property
    def capability_metadata(self) -> CapabilityEntry:
        return get_solver_metadata(self.solver_id)

    def validate_geometry(self, request):
        g = request.geometry
        if g.dimension != "2d" or g.length_m is None or g.height_m is None:
            raise SolverValidationError("channel solver requires 2d length_m and height_m")
        nx, ny = g.grid_resolution or 21, g.grid_resolution_y or 21
        if not (5 <= nx <= 60 and 5 <= ny <= 60):
            raise SolverValidationError("channel grid dimensions must be 5-60")

    def validate_material(self, request) -> dict[str, Any]:
        try:
            return {"density": materials.get_property(request.material.name, "density").value,
                    "viscosity": materials.get_property(request.material.name, "dynamic_viscosity").value}
        except (materials.MaterialNotFoundError, materials.MaterialPropertyNotFoundError) as exc:
            raise SolverValidationError(str(exc)) from exc

    def validate_boundary_conditions(self, request):
        gradient = request.boundary_conditions.pressure_gradient_pa_m
        if gradient is None or gradient >= 0:
            raise SolverValidationError("pressure_gradient_pa_m must be negative for +x channel flow")
        if abs(gradient) > 1e6:
            raise SolverValidationError("pressure gradient exceeds bounded magnitude 1e6 Pa/m")

    def prepare_model(self, request, material_properties):
        height = request.geometry.height_m
        gradient = request.boundary_conditions.pressure_gradient_pa_m
        mean_velocity = -gradient * height**2 / (12.0 * material_properties["viscosity"])
        reynolds = material_properties["density"] * mean_velocity * (2.0 * height) / material_properties["viscosity"]
        if reynolds >= 2000:
            raise SolverValidationError(f"Re={reynolds:.1f} is outside the declared laminar scope (Re < 2000)")
        return {**material_properties, "gradient": gradient, "reynolds": reynolds}

    def generate_or_import_mesh(self, request, model):
        g = request.geometry
        return (np.linspace(0, g.length_m, g.grid_resolution or 21),
                np.linspace(0, g.height_m, g.grid_resolution_y or 21))

    def solve(self, request, model, mesh):
        x, y = mesh
        ny, dy = y.size, float(y[1]-y[0])
        matrix = np.zeros((ny, ny)); rhs = np.zeros(ny)
        matrix[0,0] = matrix[-1,-1] = 1.0
        for j in range(1, ny-1):
            matrix[j,j-1], matrix[j,j], matrix[j,j+1] = 1.0, -2.0, 1.0
            rhs[j] = model["gradient"] * dy**2 / model["viscosity"]
        profile = np.linalg.solve(matrix, rhs)
        momentum_residual = float(np.max(np.abs(matrix @ profile-rhs)))
        u = np.repeat(profile[:,None], x.size, axis=1)
        v = np.zeros_like(u)
        pressure = np.repeat((model["gradient"] * x)[None,:], y.size, axis=0)
        # Fully developed u(y), v=0 gives discrete divergence exactly zero.
        mass_residual = float(np.max(np.abs(np.gradient(u, x, axis=1)+np.gradient(v, y, axis=0))))
        return {"x":x,"y":y,"u":u,"v":v,"pressure":pressure,"momentum_residual":momentum_residual,
                "mass_residual":mass_residual,"reynolds":model["reynolds"],"mean_velocity":float(np.trapezoid(profile,y)/request.geometry.height_m)}

    def calculate_residual(self, raw_result):
        return max(raw_result["momentum_residual"], raw_result["mass_residual"])

    def check_convergence(self, raw_result):
        residual = self.calculate_residual(raw_result)
        return ConvergenceStatus(converged=residual <= 1e-9, iterations=1, residual=residual, tolerance=1e-9)

    def extract_metrics(self, raw_result):
        speed = np.hypot(raw_result["u"],raw_result["v"])
        return ({"maximum_velocity_m_s":float(speed.max()),"mean_velocity_m_s":raw_result["mean_velocity"],
                 "reynolds_number":raw_result["reynolds"],"mass_conservation_residual_s_1":raw_result["mass_residual"],
                 "momentum_algebraic_residual":raw_result["momentum_residual"]},speed.ravel().tolist(),[int(np.argmax(speed))])

    def extract_field_outputs(self, raw_result, request):
        axes=[{"name":"y","unit":"m","values":raw_result["y"].tolist()},
              {"name":"x","unit":"m","values":raw_result["x"].tolist()}]
        meta={"dimension":"2d","structured":True,"fully_developed":True,"reynolds_number":raw_result["reynolds"]}
        return [NumericalFieldOutput(variable_name="velocity_x",unit="m/s",values=raw_result["u"],axes=axes,grid_metadata=meta),
                NumericalFieldOutput(variable_name="velocity_y",unit="m/s",values=raw_result["v"],axes=axes,grid_metadata=meta),
                NumericalFieldOutput(variable_name="velocity_magnitude",unit="m/s",values=np.hypot(raw_result["u"],raw_result["v"]),axes=axes,grid_metadata=meta),
                NumericalFieldOutput(variable_name="pressure",unit="Pa",values=raw_result["pressure"],axes=axes,grid_metadata=meta)]

    def return_assumptions(self):
        return ["Steady, incompressible, Newtonian, laminar flow.","Fully developed flow between infinite parallel plates.",
                "No-slip walls; constant properties; pressure-driven +x flow."]

    def return_warnings(self):
        return ["Not turbulence, compressible flow, external aerodynamics, arbitrary-CAD flow, or industrial CFD."]
