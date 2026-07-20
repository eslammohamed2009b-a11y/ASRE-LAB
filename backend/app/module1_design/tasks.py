"""
Module 1 — async batch design generation.

Reuses the existing Celery application (`app.core.celery_app.celery_app`) -
the same one already wired up for the integrated pipeline
(`app.pipeline_tasks.run_pipeline_task`) - instead of introducing a second,
unrelated job system. The actual generation logic lives in a plain,
synchronous function (`run_batch_generation`) so it can be:

- called directly by unit/integration tests without any Celery/Redis
  dependency,
- called synchronously in Celery "eager" mode
  (`CELERY_TASK_ALWAYS_EAGER=True`) for deterministic local/CI test runs,
- dispatched for real via `generate_batch_task.delay(...)` against a real
  Redis broker + separate worker process in production.

Eager-mode execution proves the task/queue plumbing and the real CadQuery
generation path work end to end in-process. It is NOT proof that a
separate Celery worker process consuming a real Redis broker works - that
requires live infrastructure (Docker Compose: see `docker-compose.yml`)
and is marked BLOCKED in this environment (no Docker/Redis installed).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import cadquery

from app.core.celery_app import celery_app
from app.core.repository import PersistenceRepository, get_repository
from app.core.storage import FileStorage, build_object_key, get_storage
from app.module1_design.cadquery_engine import generate_model
from app.module1_design.multiprocessing_generator import _build_variation_params
from app.module1_design.schemas import DesignParameters

logger = logging.getLogger(__name__)


def _cleanup_scratch_files(result: dict[str, Any]) -> None:
    for key in ("stl_path", "step_path"):
        path_str = result.get(key)
        if not path_str:
            continue
        try:
            Path(path_str).unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to clean up scratch export file after upload", exc_info=True)


def _generate_one_variant(
    *,
    repo: PersistenceRepository,
    storage: FileStorage,
    experiment_id: str,
    user_id: str,
    variation_index: int,
    params: DesignParameters,
) -> None:
    """Generate one real CadQuery variant, upload its files through
    FileStorage, and persist design_model + design_file rows. Raises on
    failure - the caller is responsible for catching per-variant so one
    failed design does not abort the whole batch."""
    result = generate_model(params)
    design_id = result["design_id"]

    design_model_id = repo.create_design_model(
        experiment_id=experiment_id,
        user_id=user_id,
        geometry_family=params.geometry_type.value,
        parameters=result["params"],
        units={"length": "m", "angle": "deg"},
        variation_index=variation_index,
        generation_status="completed",
        cadquery_version=getattr(cadquery, "__version__", "unknown"),
    )

    try:
        for idx, key in enumerate(("stl_path", "step_path")):
            scratch_path = Path(result[key])
            object_key = build_object_key(user_id, experiment_id, design_id, scratch_path.name)
            checksum = storage.calculate_checksum(scratch_path)
            size_bytes = scratch_path.stat().st_size
            storage.save_file(object_key, scratch_path)
            # The STL file keeps `design_id` as its own row id for backward
            # compatibility with the single-file `/api/design/export/{id}`
            # download route; secondary files (STEP) get their own id.
            file_id = design_id if key == "stl_path" else f"{design_id}-{key}"
            import uuid as _uuid

            if key != "stl_path":
                file_id = str(_uuid.uuid4())
            repo.record_design_file(
                design_id=file_id,
                owner_id=user_id,
                experiment_id=experiment_id,
                design_model_id=design_model_id,
                file_format=scratch_path.suffix.lstrip(".") or "bin",
                storage_provider="supabase" if type(storage).__name__ == "SupabaseStorage" else "local",
                object_key=object_key,
                file_size_bytes=size_bytes,
                checksum_sha256=checksum,
                media_type="model/stl" if key == "stl_path" else "model/step",
            )
    finally:
        _cleanup_scratch_files(result)


def run_batch_generation(
    job_id: str,
    experiment_id: str,
    user_id: str,
    base_params: DesignParameters,
    variation_count: int,
    vary_fields: list[str],
    variation_range_pct: float,
) -> dict[str, Any]:
    """The real, synchronous batch-generation loop. Safe to call directly
    (tests) or from inside a Celery task (`generate_batch_task` below)."""
    repo = get_repository()
    storage = get_storage()

    job = repo.get_job(job_id)
    if job is None:
        raise ValueError(f"Unknown job_id: {job_id}")

    if job.status == "cancelled":
        # Cancelled before this worker ever picked it up - do not
        # overwrite that terminal state with "running".
        return {"job_id": job_id, "status": "cancelled", "completed_count": 0, "failed_count": 0}

    repo.update_job(job_id, status="running", started_at=_now_iso())

    completed = 0
    failed = 0
    cancelled = False

    for idx in range(variation_count):
        # Cooperative cancellation check between variants. Real mid-task
        # cancellation requires a live worker process polling this; in
        # Celery eager mode the whole task runs synchronously in the
        # caller's thread before `.delay()` even returns, so this branch is
        # only reachable if the job was already marked cancelled by a
        # concurrent request (still a real, testable behavior).
        current = repo.get_job(job_id)
        if current is not None and current.status == "cancelled":
            cancelled = True
            break

        variant_params = _build_variation_params(base_params, vary_fields, variation_range_pct)
        try:
            _generate_one_variant(
                repo=repo,
                storage=storage,
                experiment_id=experiment_id,
                user_id=user_id,
                variation_index=idx,
                params=variant_params,
            )
            completed += 1
        except Exception:
            logger.warning("Batch variant %s failed for job %s", idx, job_id, exc_info=True)
            failed += 1

        progress = int(round(100 * (completed + failed) / variation_count))
        repo.update_job(job_id, completed_count=completed, failed_count=failed, progress_percent=progress)

    if cancelled:
        final_status = "cancelled"
    elif failed == 0:
        final_status = "completed"
    elif completed == 0:
        final_status = "failed"
    else:
        final_status = "partial_failure"

    repo.update_job(
        job_id,
        status=final_status,
        completed_count=completed,
        failed_count=failed,
        progress_percent=int(round(100 * (completed + failed) / variation_count)) if variation_count else 100,
        finished_at=_now_iso(),
    )
    return {"job_id": job_id, "status": final_status, "completed_count": completed, "failed_count": failed}


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


@celery_app.task(
    name="design.generate_batch_task",
    bind=True,
    max_retries=0,
    soft_time_limit=3000,
    time_limit=3600,
)
def generate_batch_task(
    self,
    job_id: str,
    experiment_id: str,
    user_id: str,
    base_params: dict,
    variation_count: int,
    vary_fields: list[str],
    variation_range_pct: float,
) -> dict[str, Any]:
    try:
        params = DesignParameters(**base_params)
        return run_batch_generation(
            job_id=job_id,
            experiment_id=experiment_id,
            user_id=user_id,
            base_params=params,
            variation_count=variation_count,
            vary_fields=vary_fields,
            variation_range_pct=variation_range_pct,
        )
    except Exception as exc:
        # Never leak a raw stack trace to a client polling job status - the
        # DB row gets a safe, generic message; the real exception stays in
        # server-side logs only.
        logger.error("Batch generation task crashed for job %s", job_id, exc_info=True)
        try:
            get_repository().update_job(
                job_id,
                status="failed",
                error_code="batch_generation_error",
                safe_error_message="Batch generation failed unexpectedly. Please retry.",
                finished_at=_now_iso(),
            )
        except Exception:
            logger.error("Failed to persist failure state for job %s", job_id, exc_info=True)
        raise
