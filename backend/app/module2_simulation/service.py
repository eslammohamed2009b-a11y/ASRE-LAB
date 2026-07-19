from app.module2_simulation.schemas import AnalysisType, SimulationRunRequest, SimulationRunResponse
from app.module2_simulation.solver_registry import UnsupportedAnalysisError, is_supported
from app.module2_simulation.solvers.base_solver import Mesh
from app.module2_simulation.solvers.thermal_solver import ThermalSolver


def run_simulation_service(payload: SimulationRunRequest) -> SimulationRunResponse:
    analysis = payload.analysis_type

    if not is_supported(analysis.value):
        # Do not fabricate a result for structural/CFD analyses: today they are only
        # simplified closed-form formulas, not validated numerical solvers. Raising here
        # keeps the API from misrepresenting engineering fidelity (see solver_registry.py).
        raise UnsupportedAnalysisError(analysis.value)

    mesh = Mesh(
        nodes=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)],
        elements=[(0, 1, 2, 3)],
    )

    boundary_conditions = {
        **payload.boundary_conditions,
        "design_id": payload.design_id,
        "geometry_type": payload.geometry_type,
    }

    result = ThermalSolver().solve(mesh, payload.material, boundary_conditions)
    return SimulationRunResponse(**result.model_dump())
