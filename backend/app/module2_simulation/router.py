from fastapi import APIRouter, Depends, HTTPException
from celery.result import AsyncResult

from app.module2_simulation.simulation_advisor import recommend_analyses, supported_analyses
from app.core.auth import get_current_user
from app.module2_simulation.schemas import (
    AdvisorRequest,
    AdvisorResponse,
    AnalysisType,
    SimulationRunRequest,
    SimulationRunResponse,
)
from app.module2_simulation.service import run_simulation_service
from app.module2_simulation.solver_registry import UnsupportedAnalysisError
from app.module2_simulation.tasks import run_simulation_task
from app.core.repository import get_repository

router = APIRouter(
    prefix="/api/simulate",
    tags=["Module 2 - Legacy Simulation Compatibility"],
    dependencies=[Depends(get_current_user)],
    deprecated=True,
)


@router.post(
    "/advisor",
    response_model=AdvisorResponse,
    summary="Recommend analyses for model type",
    description="Returns suggested simulation analyses based on geometry category.",
)
def advisor(payload: AdvisorRequest) -> AdvisorResponse:
    recommended = recommend_analyses(payload.model_type)
    return AdvisorResponse(recommended=recommended, supported=supported_analyses(recommended))


@router.post(
    "/run",
    response_model=SimulationRunResponse,
    summary="Run simulation synchronously",
    description=(
        "Deprecated compatibility endpoint for the original thermal-only interface. "
        "Use /api/simulations for authoritative persisted solver execution."
    ),
)
def run_simulation(payload: SimulationRunRequest) -> SimulationRunResponse:
    try:
        return run_simulation_service(payload)
    except UnsupportedAnalysisError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc


@router.post(
    "/run-async",
    summary="Queue simulation asynchronously",
    description="Enqueues simulation in Celery and returns job id.",
)
def run_simulation_async(payload: SimulationRunRequest) -> dict:
    task = run_simulation_task.delay(payload.model_dump())
    return {"job_id": task.id, "status": "queued"}


@router.get(
    "/jobs/{job_id}",
    summary="Get async simulation job status",
    description="Returns queued/running/succeeded/failed and includes result or error.",
)
def get_simulation_job_status(job_id: str) -> dict:
    task = AsyncResult(job_id)
    response = {"job_id": job_id, "status": task.status.lower()}
    if task.successful():
        response["result"] = task.result
    elif task.failed():
        response["error"] = str(task.result)
    return response


# -- new unified simulations API (Phase C8) ------------------------------------------------
# Mounted alongside (not instead of) the legacy `/api/simulate/*` router above -
# see `app.main` and the module-level note in `app.module2_simulation.schemas`.
from fastapi import Header  # noqa: E402

from app.module2_simulation.materials import MaterialNotFoundError, MaterialPropertyNotFoundError  # noqa: E402
from app.module2_simulation.schemas import (  # noqa: E402
    CapabilitiesResponse,
    RecommendRequest,
    RecommendResponse,
    SimulationCreateRequest,
    SimulationJobResponse,
    SimulationResultsResponse,
    FieldResultMetadataResponse,
    ScientificEvidenceResponse,
)
from app.module2_simulation.service import (  # noqa: E402
    SimulationNotFoundError,
    SimulationRateLimitError,
    SimulationWorkerUnavailableError,
    cancel_simulation_service,
    create_simulation_job_service,
    get_simulation_results_service,
    get_simulation_status_service,
    list_field_results_service,
    get_field_result_service,
    download_field_result_service,
)
from app.core.storage import StorageError  # noqa: E402
from fastapi.responses import Response  # noqa: E402
import csv as _csv  # noqa: E402
import io as _io  # noqa: E402
import json as _json  # noqa: E402
from dataclasses import asdict as _asdict  # noqa: E402
from app.module2_simulation.simulation_advisor import recommend_from_registry  # noqa: E402
from app.module2_simulation.solver_registry import (  # noqa: E402
    UnknownSolverError,
    UnsupportedCapabilityError,
    list_solvers,
)
from app.module2_simulation.solvers.base_solver import SolverValidationError  # noqa: E402

simulations_router = APIRouter(
    prefix="/api/simulations",
    tags=["Module 2 - Multi-Physics Simulations"],
    dependencies=[Depends(get_current_user)],
)


@simulations_router.get(
    "/capabilities",
    response_model=CapabilitiesResponse,
    summary="List every registered solver's declared capabilities",
    description=(
        "Single source of truth for what each solver family can and cannot do - governing "
        "equations, supported dimensions/materials/boundary conditions, known limitations, and "
        "benchmark test references. A solver's `implementation_status` here is always accurate: "
        "'real' solvers are backed by a validated numerical method, 'prototype'/'planned' solvers "
        "have no validated result and this API refuses to return one for them (see POST /)."
    ),
)
def get_capabilities() -> CapabilitiesResponse:
    return CapabilitiesResponse(solvers=list_solvers())


@simulations_router.post(
    "/recommend",
    response_model=RecommendResponse,
    summary="Recommend solvers for a geometry category",
    description="Registry-backed recommendations; each recommendation's status is derived directly "
    "from the capability registry, never hand-picked.",
)
def recommend(payload: RecommendRequest) -> RecommendResponse:
    return recommend_from_registry(payload)


@simulations_router.post(
    "",
    response_model=SimulationJobResponse,
    status_code=202,
    summary="Queue a new simulation job",
    description=(
        "Validates the requested solver is 'real' (never accepts a request for a prototype/planned "
        "solver), persists a queued job + an immutable snapshot of its inputs, and dispatches the "
        "solve to Celery. Returns immediately with a simulation_id for polling."
    ),
)
def create_simulation(
    payload: SimulationCreateRequest,
    current_user: dict = Depends(get_current_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> SimulationJobResponse:
    try:
        return create_simulation_job_service(payload, current_user["id"], idempotency_key)
    except (UnknownSolverError, UnsupportedCapabilityError, SolverValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (MaterialNotFoundError, MaterialPropertyNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SimulationRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except SimulationWorkerUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@simulations_router.get(
    "/{simulation_id}",
    response_model=SimulationJobResponse,
    summary="Get simulation job status",
    description="Returns the persisted status/progress of a simulation job. Owner-only.",
)
def get_simulation(simulation_id: str, current_user: dict = Depends(get_current_user)) -> SimulationJobResponse:
    try:
        return get_simulation_status_service(simulation_id, current_user["id"])
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Simulation not found") from exc


@simulations_router.post(
    "/{simulation_id}/cancel",
    response_model=SimulationJobResponse,
    summary="Cancel a simulation job",
    description="Cooperative cancellation: marks the job 'cancelled' if not already in a terminal state. Owner-only.",
)
def cancel_simulation(simulation_id: str, current_user: dict = Depends(get_current_user)) -> SimulationJobResponse:
    try:
        return cancel_simulation_service(simulation_id, current_user["id"])
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Simulation not found") from exc


@simulations_router.get(
    "/{simulation_id}/results",
    response_model=SimulationResultsResponse,
    summary="Get persisted simulation results",
    description="Returns the job status plus its persisted result payload (null until completed). Owner-only.",
)
def get_simulation_results(
    simulation_id: str, current_user: dict = Depends(get_current_user)
) -> SimulationResultsResponse:
    try:
        return get_simulation_results_service(simulation_id, current_user["id"])
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Simulation not found") from exc


@simulations_router.get(
    "/{simulation_id}/evidence",
    response_model=list[ScientificEvidenceResponse],
    summary="List authoritative scientific evidence for a simulation",
    description="Returns server-generated, typed scientific provenance for an owner-scoped persisted simulation.",
)
def list_simulation_evidence(
    simulation_id: str, current_user: dict = Depends(get_current_user)
) -> list[ScientificEvidenceResponse]:
    from app.module2_simulation.service import list_simulation_evidence_service

    try:
        return list_simulation_evidence_service(simulation_id, current_user["id"])
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Simulation not found") from exc


@simulations_router.get(
    "/{simulation_id}/fields",
    response_model=list[FieldResultMetadataResponse],
    summary="List persisted scientific field artifacts",
)
def list_simulation_fields(simulation_id: str, current_user: dict = Depends(get_current_user)):
    try:
        return list_field_results_service(simulation_id, current_user["id"])
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Simulation not found") from exc


@simulations_router.get(
    "/{simulation_id}/fields/{field_result_id}",
    response_model=FieldResultMetadataResponse,
    summary="Get scientific field metadata",
)
def get_simulation_field(simulation_id: str, field_result_id: str, current_user: dict = Depends(get_current_user)):
    try:
        record = get_field_result_service(simulation_id, field_result_id, current_user["id"])
        return FieldResultMetadataResponse(
            id=record.id, simulation_id=record.simulation_id, variable_name=record.variable_name,
            unit=record.unit, format=record.format, format_version=record.format_version,
            dimensions=record.dimensions, axes=record.axes, array_shape=record.array_shape,
            grid_metadata=record.grid_metadata, checksum_sha256=record.checksum_sha256,
            byte_size=record.byte_size, minimum=record.minimum, maximum=record.maximum,
            mean=record.mean, preview=record.preview, reproducibility_hash=record.reproducibility_hash,
            created_at=record.created_at,
        )
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Field result not found") from exc


@simulations_router.get(
    "/{simulation_id}/fields/{field_result_id}/download",
    summary="Download an owner-protected compressed field artifact",
)
def download_simulation_field(simulation_id: str, field_result_id: str, current_user: dict = Depends(get_current_user)):
    try:
        return download_field_result_service(simulation_id, field_result_id, current_user["id"])
    except (SimulationNotFoundError, StorageError) as exc:
        raise HTTPException(status_code=404, detail="Field result not found") from exc


@simulations_router.get("/{simulation_id}/export/{fmt}", summary="Export persisted simulation data")
def export_simulation_data(
    simulation_id: str, fmt: str, current_user: dict = Depends(get_current_user),
):
    repo = get_repository()
    job = repo.get_simulation_job(simulation_id)
    if job is None or job.user_id != current_user["id"]:
        raise HTTPException(status_code=404, detail="Simulation not found")
    simulation_input = repo.get_simulation_input(simulation_id)
    result = repo.get_simulation_result(simulation_id)
    if result is None:
        raise HTTPException(status_code=409, detail="Simulation result is not available")
    fields = repo.list_field_results(simulation_id)
    if fmt == "json":
        body = _json.dumps({
            "job": _asdict(job), "input": _asdict(simulation_input) if simulation_input else None,
            "result": _asdict(result), "fields": [_asdict(field) for field in fields],
        }, sort_keys=True, indent=2).encode()
        media_type, extension = "application/json", "json"
    elif fmt == "csv":
        buffer = _io.StringIO()
        writer = _csv.writer(buffer)
        writer.writerow(["simulation_id", "design_id", "solver_id", "metric", "value"])
        for metric, value in sorted(result.summary_metrics.items()):
            writer.writerow([simulation_id, job.design_id or "", result.solver_id, metric, value])
        body = buffer.getvalue().encode()
        media_type, extension = "text/csv", "csv"
    else:
        raise HTTPException(status_code=404, detail="Unsupported export format")
    return Response(
        content=body, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="simulation-{simulation_id}.{extension}"'},
    )
