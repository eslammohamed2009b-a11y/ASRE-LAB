from fastapi import APIRouter, Depends
from celery.result import AsyncResult

from app.module2_simulation.simulation_advisor import recommend_analyses
from app.core.auth import get_current_user
from app.module2_simulation.schemas import (
    AdvisorRequest,
    AdvisorResponse,
    AnalysisType,
    SimulationRunRequest,
    SimulationRunResponse,
)
from app.module2_simulation.service import run_simulation_service
from app.module2_simulation.tasks import run_simulation_task

router = APIRouter(
    prefix="/api/simulate",
    tags=["Module 2 - Automated Simulation Lab"],
    dependencies=[Depends(get_current_user)],
)


@router.post(
    "/advisor",
    response_model=AdvisorResponse,
    summary="Recommend analyses for model type",
    description="Returns suggested simulation analyses based on geometry category.",
)
def advisor(payload: AdvisorRequest) -> AdvisorResponse:
    return AdvisorResponse(recommended=recommend_analyses(payload.model_type))


@router.post(
    "/run",
    response_model=SimulationRunResponse,
    summary="Run simulation synchronously",
    description="Executes selected analysis immediately and returns field/summary metrics.",
)
def run_simulation(payload: SimulationRunRequest) -> SimulationRunResponse:
    return run_simulation_service(payload)


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
