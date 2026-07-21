from app.core.celery_app import celery_app
from app.module2_simulation.schemas import SimulationRunRequest
from app.module2_simulation.service import run_simulation_service


@celery_app.task(name="module2.run_simulation_task")
def run_simulation_task(payload: dict) -> dict:
    request = SimulationRunRequest(**payload)
    return run_simulation_service(request).model_dump()


# -- new unified async job execution (Phase C8) ------------------------------------------------
import logging as _logging
from datetime import datetime, timezone
from typing import Any

from app.core.repository import SimulationResultRecord, get_repository
from app.module2_simulation.schemas import (
    BoundaryConditions,
    Geometry,
    InitialConditions,
    MaterialSelection,
    NumericalSettings,
)
from app.module2_simulation.schemas import SimulationCreateRequest as _SimulationCreateRequest
from app.module2_simulation.solvers.base_solver import SolverValidationError
from app.module2_simulation.field_results import persist_field_result
from app.core.storage import get_storage

logger = _logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_simulation_job(
    simulation_id: str,
    solver_id: str,
    material_name: str,
    geometry: dict[str, Any],
    boundary_conditions: dict[str, Any],
    initial_conditions: dict[str, Any],
    numerical_settings: dict[str, Any],
    experiment_id: str | None = None,
    design_id: str | None = None,
) -> dict[str, Any]:
    """The real, synchronous solve for a persisted `simulation_jobs` row.
    Safe to call directly (tests) or from inside a Celery task
    (`run_simulation_job_task` below) - mirrors
    `app.module1_design.tasks.run_batch_generation`'s eager/direct/`.delay()`
    triple-callable pattern."""
    from app.module2_simulation.service import SOLVER_CLASSES  # local import avoids an import cycle

    repo = get_repository()
    job = repo.get_simulation_job(simulation_id)
    if job is None:
        raise ValueError(f"Unknown simulation_id: {simulation_id}")

    if job.status == "cancelled":
        # Cancelled before this worker picked it up - do not overwrite that
        # terminal state with "running".
        return {"simulation_id": simulation_id, "status": "cancelled"}

    repo.update_simulation_job(simulation_id, status="running", started_at=_now_iso(), progress_percent=10)

    request = _SimulationCreateRequest(
        solver_id=solver_id,
        experiment_id=experiment_id,
        design_id=design_id,
        material=MaterialSelection(name=material_name),
        geometry=Geometry(**geometry),
        boundary_conditions=BoundaryConditions(**boundary_conditions),
        initial_conditions=InitialConditions(**initial_conditions),
        numerical_settings=NumericalSettings(**numerical_settings),
    )

    solver_cls = SOLVER_CLASSES[solver_id]
    try:
        result, numerical_fields = solver_cls().run_with_fields(request)
    except SolverValidationError as exc:
        repo.update_simulation_job(
            simulation_id,
            status="failed",
            error_code="validation_error",
            safe_error_message=str(exc),
            progress_percent=100,
            finished_at=_now_iso(),
        )
        return {"simulation_id": simulation_id, "status": "failed", "error": str(exc)}
    except Exception:
        # Never leak internal exception details to the client - log the
        # full traceback server-side, persist only a safe generic message.
        logger.error("Simulation job %s failed unexpectedly", simulation_id, exc_info=True)
        repo.update_simulation_job(
            simulation_id,
            status="failed",
            error_code="internal_error",
            safe_error_message="The solver failed unexpectedly. No result was produced.",
            progress_percent=100,
            finished_at=_now_iso(),
        )
        return {"simulation_id": simulation_id, "status": "failed"}

    from app.core.config import settings as _settings

    field_records = []
    try:
        storage = get_storage()
        for numerical_field in numerical_fields:
            field_records.append(persist_field_result(
                repository=repo, storage=storage, user_id=job.user_id,
                experiment_id=experiment_id or "unassigned", simulation_id=simulation_id,
                variable_name=numerical_field.variable_name, unit=numerical_field.unit,
                axes=numerical_field.axes, values=numerical_field.values,
                solver_id=result.solver_id, solver_version=result.solver_version,
                grid_metadata={
                    **numerical_field.grid_metadata,
                    "assumptions": result.assumptions,
                    "warnings": result.warnings,
                    "convergence": result.convergence.model_dump(),
                },
            ))
    except Exception:
        logger.error("Scientific field persistence failed for simulation %s", simulation_id, exc_info=True)
        repo.update_simulation_job(
            simulation_id, status="partial_failure", progress_percent=100,
            error_code="field_persistence_error",
            safe_error_message="Scalar results completed, but one or more field artifacts could not be persisted.",
            finished_at=_now_iso(),
        )

    repo.record_simulation_result(
        SimulationResultRecord(
            simulation_id=simulation_id,
            solver_id=result.solver_id,
            solver_version=result.solver_version,
            governing_equations=result.governing_equations,
            assumptions=result.assumptions,
            warnings=result.warnings,
            converged=result.convergence.converged,
            residual=result.convergence.residual,
            iteration_count=result.convergence.iterations,
            tolerance=result.convergence.tolerance,
            summary_metrics=result.summary_metrics,
            field_values=result.field_values,
            hotspot_node_ids=result.hotspot_node_ids,
            result_object_keys=[record.storage_object_key for record in field_records],
            application_version=_settings.APPLICATION_VERSION,
        )
    )
    latest = repo.get_simulation_job(simulation_id)
    if latest.status != "partial_failure":
        repo.update_simulation_job(simulation_id, status="completed", progress_percent=100, finished_at=_now_iso())
        status = "completed"
    else:
        status = "partial_failure"
    return {"simulation_id": simulation_id, "status": status, "field_result_count": len(field_records)}


@celery_app.task(name="module2.run_simulation_job_task", max_retries=0)
def run_simulation_job_task(**kwargs: Any) -> dict:
    return run_simulation_job(**kwargs)
