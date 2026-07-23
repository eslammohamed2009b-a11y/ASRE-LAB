"""Contract coverage for the real worker-loss/restart release probe."""

from pathlib import Path


BACKEND = Path(__file__).resolve().parents[2]


def test_celery_declares_late_ack_worker_loss_redelivery_policy():
    celery_config = (BACKEND / "app/core/celery_app.py").read_text(encoding="utf-8")

    assert "task_acks_late=True" in celery_config
    assert "task_reject_on_worker_lost=True" in celery_config
    assert "worker_prefetch_multiplier=1" in celery_config
    assert '"visibility_timeout": settings.CELERY_BROKER_VISIBILITY_TIMEOUT' in celery_config


def test_api_and_worker_share_the_durable_repository_volume():
    compose_path = next(
        path
        for path in (BACKEND.parent / "docker-compose.yml", BACKEND / "docker-compose.yml")
        if path.exists()
    )
    compose = compose_path.read_text(encoding="utf-8")

    assert compose.count("- design-files:/data") == 2
    assert "- design-files:/data/design-files" not in compose


def test_probe_kills_active_worker_restarts_and_checks_duplicates():
    script = (BACKEND / "scripts/validate_worker_loss_recovery.ps1").read_text(encoding="utf-8")

    active = script.index('"active_checkpoint"')
    killed = script.index("docker compose kill -s SIGKILL worker", active)
    after_loss = script.index('"after_worker_loss"', killed)
    restarted = script.index("docker compose up -d worker", after_loss)
    terminal = script.index('"terminal"', restarted)
    api_restart = script.index("docker compose restart api", terminal)
    cleanup = script.index("docker compose down -v", api_restart)

    assert active < killed < after_loss < restarted < terminal < api_restart < cleanup
    assert "unique_model_ids" in script
    assert "unique_file_ids" in script
    assert "unique_object_keys" in script
    assert '$env:CELERY_BROKER_VISIBILITY_TIMEOUT = "10"' in script
