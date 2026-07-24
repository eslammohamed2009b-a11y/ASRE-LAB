from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import get_current_user
from app.module2_simulation.schemas import AnalysisType
from app.pipeline_service import (
    PipelineNotFoundError,
    cancel_pipeline_job_service,
    create_pipeline_job,
    get_pipeline_job_service,
    run_pipeline_flow,
)
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
    summary="Run the authoritative research pipeline synchronously",
    description=(
        "Persists CAD variants, executes the unified real thermal/structural reference "
        "scenarios with field artifacts, then persists deterministic Module 3 analysis."
    ),
)
def run_pipeline(payload: PipelineRunRequest, current_user: dict = Depends(get_current_user)) -> dict:
    return run_pipeline_flow(
        prompt=payload.prompt,
        variation_count=payload.variation_count,
        analyses=payload.analyses,
        user_id=current_user["id"],
    )


@router.post("/run-async", summary="Queue the authoritative research pipeline")
def run_pipeline_async(payload: PipelineRunRequest, current_user: dict = Depends(get_current_user)) -> dict:
    job_id, experiment_id = create_pipeline_job(
        payload.prompt, payload.variation_count, payload.analyses, current_user["id"]
    )
    run_pipeline_task.delay(
        {
            "job_id": job_id,
            "experiment_id": experiment_id,
            "prompt": payload.prompt,
            "variation_count": payload.variation_count,
            "analyses": [a.value for a in payload.analyses],
            "user_id": current_user["id"],
        }
    )
    return {"job_id": job_id, "experiment_id": experiment_id, "status": "queued"}


@router.get("/jobs/{job_id}", summary="Get durable pipeline job status")
def get_pipeline_job_status(job_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    try:
        return get_pipeline_job_service(job_id, current_user["id"])
    except PipelineNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Pipeline job not found") from exc


@router.post("/jobs/{job_id}/cancel", summary="Cancel a queued or running pipeline job")
def cancel_pipeline_job(job_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    try:
        return cancel_pipeline_job_service(job_id, current_user["id"])
    except PipelineNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Pipeline job not found") from exc
