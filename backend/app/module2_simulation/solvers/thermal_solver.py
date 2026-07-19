import numpy as np

from app.module2_simulation.solvers.base_solver import BaseSolver, Mesh, SolverResult


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
