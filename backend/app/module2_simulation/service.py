from app.module2_simulation.schemas import AnalysisType, SimulationRunRequest, SimulationRunResponse
from app.module2_simulation.solvers.base_solver import Mesh
from app.module2_simulation.solvers.cfd_solver import CFDSolver
from app.module2_simulation.solvers.structural_solver import StructuralSolver
from app.module2_simulation.solvers.thermal_solver import ThermalSolver


def run_simulation_service(payload: SimulationRunRequest) -> SimulationRunResponse:
    mesh = Mesh(
        nodes=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)],
        elements=[(0, 1, 2, 3)],
    )

    boundary_conditions = {
        **payload.boundary_conditions,
        "design_id": payload.design_id,
        "geometry_type": payload.geometry_type,
    }

    analysis = payload.analysis_type
    if analysis == AnalysisType.THERMAL:
        result = ThermalSolver().solve(mesh, payload.material, boundary_conditions)
    elif analysis == AnalysisType.STRUCTURAL:
        result = StructuralSolver().solve(mesh, payload.material, boundary_conditions)
    else:
        result = CFDSolver().solve(mesh, payload.material, boundary_conditions)

    return SimulationRunResponse(**result.model_dump())
