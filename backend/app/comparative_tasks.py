"""Sequential durable execution of a controlled comparative batch."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.celery_app import celery_app
from app.core.repository import get_repository
from app.core.storage import get_storage
from app.module2_simulation.tasks import run_simulation_job
from app.module3_analysis.schemas import AnalysisCreateRequest
from app.module3_analysis.service import run_experiment_analysis


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_comparative_batch(job_id: str, study_id: str, user_id: str, specifications: list[dict[str, Any]]):
    repo = get_repository()
    job = repo.get_job(job_id)
    if job is None or job.user_id != user_id:
        raise ValueError("Unknown comparative batch")
    repo.update_job(job_id, status="running", started_at=_now())
    completed = failed = 0
    storage = get_storage()
    for specification in specifications:
        current = repo.get_job(job_id)
        if current is not None and current.status == "cancelled":
            break
        outcome = run_simulation_job(**specification, repository=repo, storage=storage)
        if outcome["status"] in {"completed", "partial_failure"}:
            completed += 1
        else:
            failed += 1
        repo.update_job(
            job_id, completed_count=completed, failed_count=failed,
            progress_percent=round(90 * (completed + failed) / len(specifications)),
        )

    current = repo.get_job(job_id)
    if current is not None and current.status == "cancelled":
        return {"job_id": job_id, "status": "cancelled"}
    analysis_id = None
    analysis_error = False
    if completed:
        try:
            analysis_id = run_experiment_analysis(
                study_id, user_id, AnalysisCreateRequest(), repository=repo
            ).id
        except Exception:
            analysis_error = True
    status = "completed" if not failed and not analysis_error else "partial_failure" if completed else "failed"
    repo.update_job(
        job_id, status=status, completed_count=completed, failed_count=failed,
        progress_percent=100,
        error_code="analysis_failed" if analysis_error else "partial_failure" if failed else None,
        safe_error_message=(
            "Simulations were preserved, but automatic dataset analysis failed."
            if analysis_error else "Some simulations failed; successful results were preserved."
            if failed else None
        ),
        finished_at=_now(),
    )
    return {"job_id": job_id, "status": status, "analysis_id": analysis_id}


@celery_app.task(name="studies.run_comparative_batch", max_retries=0, soft_time_limit=3500, time_limit=3600)
def run_comparative_batch_task(**kwargs):
    return run_comparative_batch(**kwargs)
