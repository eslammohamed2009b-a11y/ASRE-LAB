"""Single owner-scoped authoritative simulation source resolver."""
from __future__ import annotations
from dataclasses import dataclass
from app.core.repository import get_repository

class SimulationSourceError(LookupError): pass

@dataclass(frozen=True)
class SimulationSource:
    simulation_id: str; user_id: str; experiment_id: str | None; design_id: str | None
    solver_id: str; solver_version: str; job_status: str; result: object

def resolve_simulation_source(simulation_id: str, user_id: str, *, require_result: bool=True) -> SimulationSource:
    repo=get_repository(); job=repo.get_simulation_job(simulation_id)
    if job is None or job.user_id != user_id: raise SimulationSourceError("Simulation not found")
    result=repo.get_simulation_result(simulation_id)
    if require_result and (result is None or not result.solver_id or not result.solver_version):
        raise SimulationSourceError("Simulation result is unavailable")
    solver_id=(result.solver_id if result else job.solver_id)
    version=(result.solver_version if result else "")
    if not solver_id: raise SimulationSourceError("Simulation solver identity is unavailable")
    return SimulationSource(simulation_id,user_id,job.experiment_id,job.design_id,solver_id,version,job.status,result)
