"""
Module 1 — async batch job status/cancel/results endpoints.

Deliberately separate from `app.pipeline_router`'s `/api/pipeline/jobs/{job_id}`
(which wraps Celery's ephemeral `AsyncResult` directly, with no DB
persistence and no ownership check - fine for the existing integrated
research pipeline, but not sufficient for durable, ownership-enforced
Module 1 batch jobs). These endpoints are backed by the persisted
`generation_jobs` table (see `app.core.repository`), not by querying
Celery/Redis state directly, so job status survives broker restarts and
is always ownership-checked.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_current_user
from app.core.repository import get_repository
from app.module1_design.schemas import (
    JobDesignFileSummary,
    JobDesignSummary,
    JobResultsResponse,
    JobStatusResponse,
)

router = APIRouter(
    prefix="/api/jobs",
    tags=["Module 1 - Async Jobs"],
    dependencies=[Depends(get_current_user)],
)


def _get_owned_job(job_id: str, user_id: str):
    repo = get_repository()
    job = repo.get_job(job_id)
    if job is None or job.user_id != user_id:
        # Fail closed: unknown id and someone else's id are indistinguishable
        # to the caller (404 either way) - never leak existence of another
        # user's job.
        raise HTTPException(status_code=404, detail="Job not found")
    return repo, job


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    summary="Get async batch job status",
    description="Returns the persisted status/progress of a generation job. Owner-only.",
)
def get_job_status(job_id: str, current_user: dict = Depends(get_current_user)):
    _repo, job = _get_owned_job(job_id, current_user["id"])
    return JobStatusResponse(
        job_id=job.id,
        experiment_id=job.experiment_id,
        status=job.status,
        requested_count=job.requested_count,
        completed_count=job.completed_count,
        failed_count=job.failed_count,
        progress_percent=job.progress_percent,
        error_code=job.error_code,
        safe_error_message=job.safe_error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.post(
    "/{job_id}/cancel",
    response_model=JobStatusResponse,
    summary="Cancel an async batch job",
    description=(
        "Cooperative cancellation: marks the job 'cancelled' if it has not already reached a "
        "terminal state. A worker already processing a variant checks this flag between variants "
        "and stops early; already-completed variants and their files are kept, not rolled back."
    ),
)
def cancel_job(job_id: str, current_user: dict = Depends(get_current_user)):
    repo, job = _get_owned_job(job_id, current_user["id"])
    terminal_states = {"completed", "failed", "cancelled", "partial_failure"}
    if job.status not in terminal_states:
        repo.update_job(job_id, status="cancelled")
        job = repo.get_job(job_id)
    return JobStatusResponse(
        job_id=job.id,
        experiment_id=job.experiment_id,
        status=job.status,
        requested_count=job.requested_count,
        completed_count=job.completed_count,
        failed_count=job.failed_count,
        progress_percent=job.progress_percent,
        error_code=job.error_code,
        safe_error_message=job.safe_error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.get(
    "/{job_id}/results",
    response_model=JobResultsResponse,
    summary="Get persisted results of an async batch job",
    description="Returns every design_model + its design_files generated so far for this job's experiment. Owner-only.",
)
def get_job_results(job_id: str, current_user: dict = Depends(get_current_user)):
    repo, job = _get_owned_job(job_id, current_user["id"])

    design_models = repo.list_design_models_for_experiment(job.experiment_id)
    design_files = repo.list_design_files_for_experiment(job.experiment_id)
    files_by_design_model: dict[str, list] = {}
    for design_file in design_files:
        if design_file.design_model_id:
            files_by_design_model.setdefault(design_file.design_model_id, []).append(design_file)

    designs = [
        JobDesignSummary(
            design_model_id=model.id,
            variation_index=model.variation_index,
            geometry_family=model.geometry_family,
            parameters=model.parameters,
            generation_status=model.generation_status,
            files=[
                JobDesignFileSummary(
                    design_file_id=f.id,
                    file_format=f.file_format,
                    object_key=f.object_key,
                    file_size_bytes=f.file_size_bytes,
                    checksum_sha256=f.checksum_sha256,
                    media_type=f.media_type,
                )
                for f in files_by_design_model.get(model.id, [])
            ],
        )
        for model in design_models
    ]

    return JobResultsResponse(job_id=job.id, status=job.status, designs=designs)
