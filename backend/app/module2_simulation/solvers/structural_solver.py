import numpy as np

from app.module2_simulation.solvers.base_solver import BaseSolver, Mesh, SolverResult


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
