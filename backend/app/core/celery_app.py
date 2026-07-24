from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "asre_lab",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_track_started=True,
    # A worker process may disappear after accepting a task. Acknowledge only
    # after completion and put abruptly-lost tasks back on Redis so durable,
    # idempotent task implementations can resume from their last checkpoint.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_transport_options={
        "visibility_timeout": settings.CELERY_BROKER_VISIBILITY_TIMEOUT,
    },
    result_backend_transport_options={
        "visibility_timeout": settings.CELERY_BROKER_VISIBILITY_TIMEOUT,
    },
    visibility_timeout=settings.CELERY_BROKER_VISIBILITY_TIMEOUT,
    result_expires=3600,
    task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
    task_eager_propagates=settings.CELERY_TASK_ALWAYS_EAGER,
)

celery_app.autodiscover_tasks(["app.module1_design", "app.module2_simulation", "app"])
