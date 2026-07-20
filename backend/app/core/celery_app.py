from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "asre_lab",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_track_started=True,
    result_expires=3600,
    task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
    task_eager_propagates=settings.CELERY_TASK_ALWAYS_EAGER,
)

celery_app.autodiscover_tasks(["app.module1_design", "app.module2_simulation", "app"])
