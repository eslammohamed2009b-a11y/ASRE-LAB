"""Single owner-scoped authoritative simulation source resolver."""
from __future__ import annotations
from dataclasses import dataclass
from app.core.repository import SimulationResultRecord, get_repository

class SimulationSourceError(LookupError):
    """Base error for an unavailable or scientifically invalid source."""

class SimulationSourceNotFoundError(SimulationSourceError):
    """Owner-safe absence: nonexistent and cross-owner sources are identical."""

class SimulationSourceIntegrityError(SimulationSourceError):
    """The owned source exists but cannot support an authoritative claim."""

@dataclass(frozen=True)
class SimulationSource:
    simulation_id: str; user_id: str; experiment_id: str | None; design_id: str | None
    solver_id: str; solver_version: str; job_status: str; result: SimulationResultRecord | None

def resolve_simulation_source(
    simulation_id: str,
    user_id: str,
    *,
    require_result: bool = True,
    require_completed_result: bool = False,
    required_summary_metric: str | None = None,
    repository=None,
) -> SimulationSource:
    """Resolve an owner-scoped simulation and optionally its authoritative result.

    Completion and metric requirements are opt-in so callers that legitimately
    inspect queued/running simulations can continue to use the shared resolver.
    """
    repo=repository or get_repository(); job=repo.get_simulation_job(simulation_id)
    if job is None or job.user_id != user_id:
        raise SimulationSourceNotFoundError("Simulation not found")
    result=repo.get_simulation_result(simulation_id)
    result_required = require_result or require_completed_result or required_summary_metric is not None
    if result_required and result is None:
        raise SimulationSourceIntegrityError("Simulation result is unavailable")
    if result is not None and (not result.solver_id or not result.solver_version):
        raise SimulationSourceIntegrityError("Simulation result solver identity is unavailable")
    if result is not None and job.solver_id and result.solver_id != job.solver_id:
        raise SimulationSourceIntegrityError("Simulation result solver contradicts its job")
    if require_completed_result:
        if job.status != "completed":
            raise SimulationSourceIntegrityError("Simulation job is not completed")
        if result is None or result.status != "completed":
            raise SimulationSourceIntegrityError("Simulation result is not scientifically available")
    if required_summary_metric is not None:
        if result is None or required_summary_metric not in result.summary_metrics:
            raise SimulationSourceIntegrityError(
                f"Simulation result does not contain required metric '{required_summary_metric}'"
            )
    solver_id=(result.solver_id if result else job.solver_id)
    version=(result.solver_version if result else "")
    if not solver_id:
        raise SimulationSourceIntegrityError("Simulation solver identity is unavailable")
    return SimulationSource(simulation_id,user_id,job.experiment_id,job.design_id,solver_id,version,job.status,result)
