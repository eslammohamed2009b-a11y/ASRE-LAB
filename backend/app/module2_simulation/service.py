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


# -- new unified orchestration (Phase C8) ------------------------------------------------
from datetime import datetime, timezone  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.repository import SimulationResultRecord, get_repository  # noqa: E402
from app.module2_simulation.materials import properties_as_dict  # noqa: E402
from app.module2_simulation.schemas import (  # noqa: E402
    ConvergenceStatus,
    SimulationCreateRequest,
    SimulationJobResponse,
    SimulationResultPayload,
    SimulationResultsResponse,
    SimulationStatus,
)
from app.module2_simulation.solver_registry import require_available  # noqa: E402
from app.module2_simulation.solvers.base_solver import EngineeringSolver  # noqa: E402
from app.module2_simulation.solvers.modal_solver import ModalSolver  # noqa: E402
from app.module2_simulation.solvers.structural_solver import StructuralLinearSolver  # noqa: E402
from app.module2_simulation.solvers.thermal_solver import ThermalConductionSolver  # noqa: E402

SOLVER_CLASSES: dict[str, type[EngineeringSolver]] = {
    "thermal_conduction_v1": ThermalConductionSolver,
    "structural_linear_1d_v1": StructuralLinearSolver,
    "modal_eigen_1d_v1": ModalSolver,
}


class SimulationNotFoundError(Exception):
    """Unknown simulation_id, or one owned by a different user - callers
    map this to a 404 (fail-closed: never distinguish the two cases)."""


class SimulationRateLimitError(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_job_response(job) -> SimulationJobResponse:
    return SimulationJobResponse(
        simulation_id=job.id,
        experiment_id=job.experiment_id,
        design_id=job.design_id,
        solver_id=job.solver_id,
        status=SimulationStatus(job.status),
        progress_percent=job.progress_percent,
        error_code=job.error_code,
        safe_error_message=job.safe_error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def create_simulation_job_service(
    request: SimulationCreateRequest, user_id: str, idempotency_key: str | None
) -> SimulationJobResponse:
    """Validates the solver is REAL (raises `UnsupportedCapabilityError`/
    `UnknownSolverError` otherwise - never fabricates a result for a
    prototype/planned solver), persists a queued job + its immutable input
    snapshot, and dispatches the actual solve to Celery. Raises
    `MaterialNotFoundError` for an unknown material."""
    require_available(request.solver_id)

    repo = get_repository()

    if idempotency_key:
        existing = repo.get_simulation_job_by_idempotency_key(user_id, idempotency_key)
        if existing is not None:
            return _to_job_response(existing)

    if repo.count_active_simulation_jobs_for_user(user_id) >= settings.MAX_CONCURRENT_SIMULATION_JOBS_PER_USER:
        raise SimulationRateLimitError(
            f"Concurrent simulation job limit reached ({settings.MAX_CONCURRENT_SIMULATION_JOBS_PER_USER} "
            "queued or running jobs per user)"
        )

    # Raises MaterialNotFoundError if unknown - fail before any job row is created.
    material_properties = properties_as_dict(request.material.name)

    simulation_id = repo.create_simulation_job(
        user_id=user_id,
        solver_id=request.solver_id,
        experiment_id=request.experiment_id,
        design_id=request.design_id,
        idempotency_key=idempotency_key,
    )
    repo.record_simulation_input(
        simulation_id=simulation_id,
        material_name=request.material.name,
        material_properties=material_properties,
        units={},
        initial_conditions=request.initial_conditions.model_dump(),
        boundary_conditions=request.boundary_conditions.model_dump(),
        numerical_settings=request.numerical_settings.model_dump(),
    )

    # Local import: avoid importing Celery/the task module (and therefore
    # requiring a broker connection at import time) for every request to
    # this router's other, synchronous endpoints - mirrors
    # `app.module1_design.router`'s `generate_batch` endpoint.
    from app.module2_simulation.tasks import run_simulation_job_task

    run_simulation_job_task.delay(
        simulation_id=simulation_id,
        solver_id=request.solver_id,
        material_name=request.material.name,
        geometry=request.geometry.model_dump(),
        boundary_conditions=request.boundary_conditions.model_dump(),
        initial_conditions=request.initial_conditions.model_dump(),
        numerical_settings=request.numerical_settings.model_dump(),
        experiment_id=request.experiment_id,
        design_id=request.design_id,
    )

    job = repo.get_simulation_job(simulation_id)
    return _to_job_response(job)


def get_simulation_status_service(simulation_id: str, user_id: str) -> SimulationJobResponse:
    repo = get_repository()
    job = repo.get_simulation_job(simulation_id)
    if job is None or job.user_id != user_id:
        raise SimulationNotFoundError(simulation_id)
    return _to_job_response(job)


def cancel_simulation_service(simulation_id: str, user_id: str) -> SimulationJobResponse:
    repo = get_repository()
    job = repo.get_simulation_job(simulation_id)
    if job is None or job.user_id != user_id:
        raise SimulationNotFoundError(simulation_id)
    terminal_states = {"completed", "failed", "cancelled"}
    if job.status not in terminal_states:
        repo.update_simulation_job(simulation_id, status="cancelled", finished_at=_now_iso())
        job = repo.get_simulation_job(simulation_id)
    return _to_job_response(job)


def get_simulation_results_service(simulation_id: str, user_id: str) -> SimulationResultsResponse:
    repo = get_repository()
    job = repo.get_simulation_job(simulation_id)
    if job is None or job.user_id != user_id:
        raise SimulationNotFoundError(simulation_id)

    result_record = repo.get_simulation_result(simulation_id)
    result_payload = None
    if result_record is not None:
        result_payload = SimulationResultPayload(
            solver_id=result_record.solver_id,
            solver_version=result_record.solver_version,
            governing_equations=result_record.governing_equations,
            assumptions=result_record.assumptions,
            warnings=result_record.warnings,
            convergence=ConvergenceStatus(
                converged=result_record.converged,
                iterations=result_record.iteration_count,
                residual=result_record.residual,
                tolerance=result_record.tolerance,
            ),
            summary_metrics=result_record.summary_metrics,
            field_values=result_record.field_values,
            hotspot_node_ids=result_record.hotspot_node_ids,
        )

    base = _to_job_response(job)
    return SimulationResultsResponse(**base.model_dump(), result=result_payload)
