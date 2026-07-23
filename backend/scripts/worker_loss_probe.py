"""Container-only driver for the real Celery worker-loss release scenario."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snapshot(job_id: str, task_id: str) -> dict:
    from celery.result import AsyncResult

    from app.core.celery_app import celery_app
    from app.core.repository import get_repository

    repo = get_repository()
    job = repo.get_job(job_id)
    if job is None:
        raise SystemExit(f"unknown job: {job_id}")
    models = repo.list_design_models_for_experiment(job.experiment_id)
    files = repo.list_design_files_for_experiment(job.experiment_id)
    return {
        "timestamp": _now(),
        "job_id": job.id,
        "task_id": task_id,
        "durable_state": job.status,
        "celery_state": AsyncResult(task_id, app=celery_app).state,
        "requested_count": job.requested_count,
        "completed_count": job.completed_count,
        "failed_count": job.failed_count,
        "progress_percent": job.progress_percent,
        "model_count": len(models),
        "file_count": len(files),
        "variation_indices": sorted(model.variation_index for model in models),
        "unique_model_ids": len({model.id for model in models}),
        "unique_file_ids": len({file.id for file in files}),
        "unique_object_keys": len({file.object_key for file in files}),
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def dispatch() -> dict:
    from app.core.repository import get_repository
    from app.module1_design.tasks import generate_batch_task

    repo = get_repository()
    user_id = "worker-loss-release-probe"
    requested_count = 4
    experiment_id = repo.create_experiment(
        user_id=user_id,
        name="deterministic worker-loss recovery probe",
        input_specification={"release_probe": True},
    )
    job_id = repo.create_job(
        experiment_id=experiment_id,
        user_id=user_id,
        job_type="design_batch_worker_loss_probe",
        requested_count=requested_count,
        idempotency_key=f"worker-loss-{uuid.uuid4()}",
    )
    task_id = str(uuid.uuid4())
    generate_batch_task.apply_async(
        task_id=task_id,
        kwargs={
            "job_id": job_id,
            "experiment_id": experiment_id,
            "user_id": user_id,
            "base_params": {"geometry_type": "pyramid", "height_m": 50},
            "variation_count": requested_count,
            "vary_fields": ["height_m"],
            "variation_range_pct": 0.1,
            # Creates a deterministic safe kill window after each durable
            # variant checkpoint without weakening production execution.
            "checkpoint_delay_seconds": 8,
        },
    )
    return {
        "timestamp": _now(),
        "event": "dispatched",
        "job_id": job_id,
        "task_id": task_id,
        "experiment_id": experiment_id,
        "requested_count": requested_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("dispatch")
    status = subparsers.add_parser("status")
    status.add_argument("job_id")
    status.add_argument("task_id")
    args = parser.parse_args()

    result = dispatch() if args.command == "dispatch" else _snapshot(args.job_id, args.task_id)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
