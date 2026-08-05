from app.core.celery_app import celery_app


def test_comparative_batch_module_is_imported_by_every_worker():
    assert "app.comparative_tasks" in celery_app.conf.imports
