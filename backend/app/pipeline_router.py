from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends
from celery.result import AsyncResult

from app.core.auth import get_current_user
from app.module2_simulation.schemas import AnalysisType
from app.pipeline_service import run_pipeline_flow
from app.pipeline_tasks import run_pipeline_task

router = APIRouter(
    prefix="/api/pipeline",
    tags=["Integrated Research Pipeline"],
    dependencies=[Depends(get_current_user)],
)


class PipelineRunRequest(BaseModel):
    prompt: str = Field(min_length=3)
    variation_count: int = Field(default=10, ge=1, le=100)
    analyses: list[AnalysisType] = Field(default_factory=lambda: [AnalysisType.THERMAL, AnalysisType.STRUCTURAL])


@router.post(
    "/run",
    summary="Run full research pipeline synchronously",
    description="Executes Module 1 -> Module 2 -> Module 3 in a single request and persists outputs when Supabase is configured.",
)
def run_pipeline(payload: PipelineRunRequest, current_user: dict = Depends(get_current_user)) -> dict:
    return run_pipeline_flow(
        prompt=payload.prompt,
        variation_count=payload.variation_count,
        analyses=payload.analyses,
        user_id=current_user["id"],
    )


@router.post(
    "/run-async",
    summary="Run full research pipeline asynchronously",
    description="Queues the end-to-end pipeline in Celery and returns a job id for polling.",
)
def run_pipeline_async(payload: PipelineRunRequest, current_user: dict = Depends(get_current_user)) -> dict:
    task = run_pipeline_task.delay(
        {
            "prompt": payload.prompt,
            "variation_count": payload.variation_count,
            "analyses": [a.value for a in payload.analyses],
            "user_id": current_user["id"],
        }
    )
    return {"job_id": task.id, "status": "queued"}


@router.get(
    "/jobs/{job_id}",
    summary="Get pipeline async job status",
    description="Returns queued/running/succeeded/failed plus result or error payload.",
)
def get_pipeline_job_status(job_id: str) -> dict:
    task = AsyncResult(job_id)
    response = {"job_id": job_id, "status": task.status.lower()}
    if task.successful():
        response["result"] = task.result
    elif task.failed():
        response["error"] = str(task.result)
    return response
