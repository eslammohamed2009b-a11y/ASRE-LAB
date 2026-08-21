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
from pydantic import ValidationError

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
from app.module2_simulation.evidence_lifecycle import persist_automatic_evidence
from app.module2_simulation.provenance import result_hash
from app.module2_simulation.materials import (
    MaterialNotFoundError,
    MaterialPropertyNotFoundError,
    properties_as_dict,
)
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
    *,
    repository=None,
    storage=None,
) -> dict[str, Any]:
    """The real, synchronous solve for a persisted `simulation_jobs` row.
    Safe to call directly (tests) or from inside a Celery task
    (`run_simulation_job_task` below) - mirrors
    `app.module1_design.tasks.run_batch_generation`'s eager/direct/`.delay()`
    triple-callable pattern."""
    from app.module2_simulation.service import SOLVER_CLASSES  # local import avoids an import cycle

    repo = repository or get_repository()
    job = repo.get_simulation_job(simulation_id)
    if job is None:
        raise ValueError(f"Unknown simulation_id: {simulation_id}")

    if job.status == "cancelled":
        # Cancelled before this worker picked it up - do not overwrite that
        # terminal state with "running".
        return {"simulation_id": simulation_id, "status": "cancelled"}

    if repo.get_simulation_input(simulation_id) is None:
        # Direct/internal callers use this same authoritative lifecycle. Keep
        # their actual invocation arguments as the immutable input snapshot.
        try:
            material_properties = properties_as_dict(material_name)
        except (MaterialNotFoundError, MaterialPropertyNotFoundError) as exc:
            repo.update_simulation_job(
                simulation_id, status="failed", error_code="validation_error",
                safe_error_message=str(exc), progress_percent=100, finished_at=_now_iso(),
            )
            return {"simulation_id": simulation_id, "status": "failed", "error": str(exc)}
        repo.record_simulation_input(
            simulation_id=simulation_id, material_name=material_name,
            material_properties=material_properties, units={},
            initial_conditions=initial_conditions,
            boundary_conditions=boundary_conditions,
            numerical_settings=numerical_settings, geometry=geometry,
        )

    existing_result = repo.get_simulation_result(simulation_id)
    if existing_result is not None:
        # A worker retry after result persistence must recover missing evidence,
        # never execute the solver or create another set of field artifacts.
        terminal_status = "completed" if existing_result.status == "completed" else "partial_failure"
        repo.update_simulation_job(simulation_id, status=terminal_status, progress_percent=100)
        try:
            records = persist_automatic_evidence(repo, simulation_id)
        except Exception:
            logger.error("Scientific evidence recovery failed for simulation %s", simulation_id, exc_info=True)
            repo.update_simulation_job(
                simulation_id, status="partial_failure", progress_percent=100,
                error_code="evidence_persistence_error",
                safe_error_message="The result is preserved, but its scientific evidence lifecycle is incomplete.",
            )
            return {"simulation_id": simulation_id, "status": "partial_failure"}
        return {
            "simulation_id": simulation_id, "status": terminal_status,
            "field_result_count": len(repo.list_field_results(simulation_id)),
            "evidence_record_count": len(records),
        }

    if repo.list_field_results(simulation_id):
        # Fields without a final result indicate an interrupted attempt. Do
        # not append a second, potentially contradictory artifact set.
        repo.update_simulation_job(
            simulation_id, status="partial_failure", progress_percent=100,
            error_code="incomplete_field_lifecycle",
            safe_error_message="Field persistence was interrupted before the final result identity was recorded.",
            finished_at=_now_iso(),
        )
        return {"simulation_id": simulation_id, "status": "partial_failure"}

    repo.update_simulation_job(simulation_id, status="running", started_at=_now_iso(), progress_percent=10)

    try:
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
        result, numerical_fields = solver_cls().run_with_fields(request)
        result.source_simulation_id = simulation_id
    except (SolverValidationError, ValidationError) as exc:
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
    field_persistence_failed = False
    try:
        storage = storage or get_storage()
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
        field_persistence_failed = True
        logger.error("Scientific field persistence failed for simulation %s", simulation_id, exc_info=True)
        repo.update_simulation_job(
            simulation_id, status="partial_failure", progress_percent=100,
            error_code="field_persistence_error",
            safe_error_message="Scalar results completed, but one or more field artifacts could not be persisted.",
            finished_at=_now_iso(),
        )

    if field_persistence_failed:
        return {
            "simulation_id": simulation_id, "status": "partial_failure",
            "field_result_count": len(field_records),
        }

    persisted_status = (
        "partial_failure"
        if repo.get_simulation_job(simulation_id).status == "partial_failure" or not result.convergence.converged
        else "completed"
    )
    if not result.convergence.converged:
        repo.update_simulation_job(
            simulation_id, status="partial_failure", progress_percent=100,
            error_code="nonconverged",
            safe_error_message="The numerical solve did not meet its declared convergence tolerance; inspect the preserved result with caution.",
            finished_at=_now_iso(),
        )
    try:
        final_result_hash = result_hash(
            solver_id=result.solver_id,
            solver_version=result.solver_version,
            input_fingerprint_value=result.validation_metadata["input_fingerprint"],
            converged=result.convergence.converged,
            iteration_count=result.convergence.iterations,
            metric=result.convergence.residual,
            tolerance=result.convergence.tolerance,
            summary_metrics=result.summary_metrics,
            validation_metadata=result.validation_metadata,
            numerical_method=result.numerical_method,
            field_checksums=[
                f"{record.variable_name}:{record.unit}:{record.reproducibility_hash}"
                for record in field_records
            ],
        )
    except Exception:
        logger.error("Final scientific result hashing failed for simulation %s", simulation_id, exc_info=True)
        repo.update_simulation_job(
            simulation_id, status="partial_failure", progress_percent=100,
            error_code="result_hash_error",
            safe_error_message="Scientific result identity could not be finalized.",
            finished_at=_now_iso(),
        )
        return {"simulation_id": simulation_id, "status": "partial_failure"}

    try:
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
                status=persisted_status,
                numerical_method=result.numerical_method,
                residual_history=result.residual_history,
                validation_metadata=result.validation_metadata,
                elapsed_time_seconds=result.elapsed_time_seconds,
                reproducibility_hash=final_result_hash,
                source_design_id=design_id,
            )
        )
    except Exception:
        logger.error("Scientific result persistence failed for simulation %s", simulation_id, exc_info=True)
        repo.update_simulation_job(
            simulation_id, status="partial_failure", progress_percent=100,
            error_code="result_persistence_error",
            safe_error_message="Field artifacts are preserved, but the final scientific result was not persisted.",
            finished_at=_now_iso(),
        )
        return {
            "simulation_id": simulation_id, "status": "partial_failure",
            "field_result_count": len(field_records),
        }
    latest = repo.get_simulation_job(simulation_id)
    if latest.status != "partial_failure":
        repo.update_simulation_job(simulation_id, status="completed", progress_percent=100, finished_at=_now_iso())
        status = "completed"
    else:
        status = "partial_failure"
    try:
        evidence_records = persist_automatic_evidence(repo, simulation_id)
    except Exception:
        logger.error("Scientific evidence persistence failed for simulation %s", simulation_id, exc_info=True)
        repo.update_simulation_job(
            simulation_id, status="partial_failure", progress_percent=100,
            error_code="evidence_persistence_error",
            safe_error_message="The result is preserved, but its scientific evidence lifecycle is incomplete.",
            finished_at=_now_iso(),
        )
        return {
            "simulation_id": simulation_id, "status": "partial_failure",
            "field_result_count": len(field_records),
        }
    return {
        "simulation_id": simulation_id, "status": status,
        "field_result_count": len(field_records),
        "evidence_record_count": len(evidence_records),
    }


@celery_app.task(name="module2.run_simulation_job_task", max_retries=0)
def run_simulation_job_task(**kwargs: Any) -> dict:
    return run_simulation_job(**kwargs)


@celery_app.task(name="module2.prepare_cfd_physics_task", max_retries=0, queue="cfd")
def prepare_cfd_physics_task(*, job_id: str, owner_id: str, payload: dict) -> dict:
    """Dedicated CFD-worker entry point; the API image never invokes OpenFOAM."""
    from app.module2_simulation.cad_cfd_execution import CFDPhysicsCreateRequest, prepare_cfd_physics

    result = prepare_cfd_physics(
        repository=get_repository(), storage=get_storage(), job_id=job_id, owner_id=owner_id,
        payload=CFDPhysicsCreateRequest.model_validate(payload),
    )
    return result.model_dump(mode="json")


@celery_app.task(name="module2.run_cad_cfd_job_task", max_retries=0, queue="cfd")
def run_cad_cfd_job_task(*, simulation_id: str) -> dict:
    """Execute only persisted certified-FV CFD jobs on the dedicated queue."""
    from app.module2_simulation.cad_cfd_execution import execute_cad_cfd_job

    return execute_cad_cfd_job(
        repository=get_repository(), storage=get_storage(), simulation_id=simulation_id,
    )
