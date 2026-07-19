"""
Module 2 — Computational Fluid Dynamics Solver (wind load).
This scaffold uses a simplified drag model as placeholder for full CFD.
"""
import numpy as np

from app.module2_simulation.solvers.base_solver import BaseSolver, Mesh, SolverResult

DRAG_COEFFICIENTS = {"pyramid": 1.2, "tower": 1.0, "bridge": 1.3, "arch": 0.9, "dome": 0.4}


class CFDSolver(BaseSolver):
    analysis_type = "wind_load"

    def solve(self, mesh: Mesh, material: str, boundary_conditions: dict) -> SolverResult:
        n = len(mesh.nodes)
        wind_speed = boundary_conditions.get("wind_speed_mps", 30.0)
        air_density = 1.225
        cd = DRAG_COEFFICIENTS.get(boundary_conditions.get("geometry_type", "tower"), 1.0)
        frontal_area = boundary_conditions.get("frontal_area_m2", 100.0)

        drag_force_n = 0.5 * air_density * wind_speed**2 * cd * frontal_area
        pressure_field = np.full(n, drag_force_n / max(frontal_area, 1))

        return SolverResult(
            analysis_type=self.analysis_type,
            design_id=boundary_conditions.get("design_id", "unknown"),
            summary_metrics={
                "total_drag_force_n": float(drag_force_n),
                "avg_surface_pressure_pa": float(pressure_field.mean()),
            },
            field_values=pressure_field.tolist(),
            hotspot_node_ids=list(range(min(5, n))),
        )
