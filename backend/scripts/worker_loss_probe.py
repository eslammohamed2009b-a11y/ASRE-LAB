"""Container-only driver for the real Celery worker-loss release scenario."""

from __future__ import annotations

import argparse
import json
import os
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
    user_id = os.environ.get("SUPABASE_TEST_USER_A_ID", "worker-loss-release-probe")
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


def _cleanup_experiment(repo, experiment_id: str) -> int:
    from app.core.storage import get_storage

    storage = get_storage()
    client = getattr(repo, "_client", None)
    if client is None:
        raise SystemExit("cleanup is supported only by the disposable Supabase staging repository")
    object_keys = {
        item.object_key for item in repo.list_design_files_for_experiment(experiment_id)
    }
    simulations = client.table("simulation_jobs").select("id").eq(
        "experiment_id", experiment_id
    ).execute().data
    simulation_ids = [item["id"] for item in simulations]
    if simulation_ids:
        fields = client.table("simulation_field_results").select(
            "storage_object_key"
        ).in_("simulation_id", simulation_ids).execute().data
        object_keys.update(item["storage_object_key"] for item in fields)
    for object_key in object_keys:
        storage.delete_file(object_key)
    client.table("experiments").delete().eq("id", experiment_id).execute()
    return len(object_keys)


def cleanup(job_id: str, task_id: str) -> dict:
    """Remove one abandoned staging probe and only its exact broker delivery."""
    import redis

    from app.core.config import settings
    from app.core.repository import get_repository

    repo = get_repository()
    job = repo.get_job(job_id)
    if job is None:
        return {"event": "cleanup", "job_id": job_id, "deliveries_removed": 0}
    broker = redis.Redis.from_url(settings.CELERY_BROKER_URL)
    deliveries_removed = 0
    for delivery_tag, payload in broker.hgetall("unacked").items():
        if task_id.encode() in payload:
            deliveries_removed += broker.hdel("unacked", delivery_tag)
            broker.zrem("unacked_index", delivery_tag)
    objects_removed = _cleanup_experiment(repo, job.experiment_id)
    return {
        "event": "cleanup",
        "job_id": job_id,
        "deliveries_removed": deliveries_removed,
        "objects_removed": objects_removed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("dispatch")
    status = subparsers.add_parser("status")
    status.add_argument("job_id")
    status.add_argument("task_id")
    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("job_id")
    cleanup_parser.add_argument("task_id")
    cleanup_experiment_parser = subparsers.add_parser("cleanup-experiment")
    cleanup_experiment_parser.add_argument("experiment_id")
    args = parser.parse_args()

    if args.command == "dispatch":
        result = dispatch()
    elif args.command == "cleanup":
        result = cleanup(args.job_id, args.task_id)
    elif args.command == "cleanup-experiment":
        from app.core.repository import get_repository

        result = {
            "event": "cleanup-experiment",
            "experiment_id": args.experiment_id,
            "objects_removed": _cleanup_experiment(
                get_repository(), args.experiment_id
            ),
        }
    else:
        result = _snapshot(args.job_id, args.task_id)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
