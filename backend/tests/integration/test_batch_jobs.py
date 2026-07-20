"""
Module 1 — async batch generation job integration tests.

These drive the real FastAPI app (TestClient) with the real CadQuery
kernel and Celery running in "eager" mode (`CELERY_TASK_ALWAYS_EAGER=True`,
set via `conftest.py`/fixture below), so `generate_batch_task.delay(...)`
executes synchronously in-process instead of requiring a live Redis broker
and a separate worker process.

Eager-mode execution here proves: the real CadQuery generation path,
FileStorage upload, and generation_jobs persistence/ownership all work
end-to-end. It is NOT proof that a live Redis broker + separate Celery
worker process works - that remains BLOCKED in this environment (no
Docker/Redis installed; see backend/docker-compose.yml and the final
report for the honest status of that gap).
"""
import os

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True, scope="module")
def _eager_celery():
    """Force Celery eager mode for this test module only, then restore
    whatever the app's celery_app config was before."""
    from app.core.celery_app import celery_app

    previous_env = os.environ.get("CELERY_TASK_ALWAYS_EAGER")
    os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
    previous_eager = celery_app.conf.task_always_eager
    previous_propagates = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield
    celery_app.conf.task_always_eager = previous_eager
    celery_app.conf.task_eager_propagates = previous_propagates
    if previous_env is None:
        os.environ.pop("CELERY_TASK_ALWAYS_EAGER", None)
    else:
        os.environ["CELERY_TASK_ALWAYS_EAGER"] = previous_env


def _client_as(user_id: str):
    from fastapi.testclient import TestClient

    from app.core.auth import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: {"id": user_id, "role": "researcher"}
    return TestClient(app)


def _clear_overrides():
    from app.main import app

    app.dependency_overrides.clear()


def _batch_payload(variation_count: int = 3):
    return {
        "base_params": {"geometry_type": "pyramid", "height_m": 50},
        "variation_count": variation_count,
        "vary_fields": ["height_m"],
        "variation_range_pct": 0.1,
    }


def test_generate_batch_dispatches_and_completes_with_real_cadquery():
    try:
        client = _client_as("user-batch-a")
        response = client.post("/api/design/generate-batch", json=_batch_payload(3))
        assert response.status_code == 202, response.text
        job_id = response.json()["job_id"]
        assert response.json()["status"] == "queued"

        # Eager mode: the task already ran synchronously by the time
        # .delay() returned above, so status should already be terminal.
        status_response = client.get(f"/api/jobs/{job_id}")
        assert status_response.status_code == 200
        payload = status_response.json()
        assert payload["status"] == "completed"
        assert payload["completed_count"] == 3
        assert payload["failed_count"] == 0
        assert payload["progress_percent"] == 100

        results_response = client.get(f"/api/jobs/{job_id}/results")
        assert results_response.status_code == 200
        designs = results_response.json()["designs"]
        assert len(designs) == 3
        for design in designs:
            assert design["generation_status"] == "completed"
            file_formats = {f["file_format"] for f in design["files"]}
            assert file_formats == {"stl", "step"}
            for f in design["files"]:
                assert f["file_size_bytes"] > 0
                assert f["checksum_sha256"]
    finally:
        _clear_overrides()


def test_batch_job_exceeding_max_variants_is_rejected():
    try:
        client = _client_as("user-batch-limits")
        response = client.post("/api/design/generate-batch", json=_batch_payload(9999))
        assert response.status_code == 422
    finally:
        _clear_overrides()


def test_per_user_concurrent_job_limit_is_enforced():
    from app.core.config import settings
    from app.core.repository import get_repository

    user_id = "user-job-limit"
    repo = get_repository()
    for index in range(settings.MAX_CONCURRENT_JOBS_PER_USER):
        experiment_id = repo.create_experiment(user_id=user_id, name=f"active-job-{index}")
        repo.create_job(
            experiment_id=experiment_id,
            user_id=user_id,
            job_type="design_batch",
            requested_count=1,
        )

    try:
        client = _client_as(user_id)
        response = client.post("/api/design/generate-batch", json=_batch_payload(1))
        assert response.status_code == 429
        assert "Concurrent job limit reached" in response.json()["detail"]
    finally:
        _clear_overrides()


def test_job_status_requires_ownership():
    try:
        client_a = _client_as("user-batch-owner")
        response = client_a.post("/api/design/generate-batch", json=_batch_payload(1))
        job_id = response.json()["job_id"]

        client_b = _client_as("user-batch-intruder")
        cross_user_status = client_b.get(f"/api/jobs/{job_id}")
        assert cross_user_status.status_code == 404

        cross_user_results = client_b.get(f"/api/jobs/{job_id}/results")
        assert cross_user_results.status_code == 404

        cross_user_cancel = client_b.post(f"/api/jobs/{job_id}/cancel")
        assert cross_user_cancel.status_code == 404
    finally:
        _clear_overrides()


def test_unknown_job_id_returns_404():
    try:
        client = _client_as("user-batch-unknown")
        response = client.get("/api/jobs/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404
    finally:
        _clear_overrides()


def test_cancel_job_before_dispatch_prevents_processing():
    """Cooperative cancellation: mark the job cancelled via the repository
    directly (simulating a cancel request that lands before a worker starts
    processing it), then run the batch generation function directly (no
    HTTP/task layer) and confirm it stops immediately without generating
    any designs."""
    from app.core.repository import get_repository
    from app.module1_design.schemas import DesignParameters
    from app.module1_design.tasks import run_batch_generation

    repo = get_repository()
    experiment_id = repo.create_experiment(user_id="user-cancel", name="cancel-test")
    job_id = repo.create_job(
        experiment_id=experiment_id, user_id="user-cancel", job_type="design_batch", requested_count=5
    )
    repo.update_job(job_id, status="cancelled")

    result = run_batch_generation(
        job_id=job_id,
        experiment_id=experiment_id,
        user_id="user-cancel",
        base_params=DesignParameters(geometry_type="pyramid", height_m=50),
        variation_count=5,
        vary_fields=["height_m"],
        variation_range_pct=0.1,
    )
    assert result["status"] == "cancelled"
    assert result["completed_count"] == 0

    job = repo.get_job(job_id)
    assert job.status == "cancelled"
    assert job.completed_count == 0


def test_idempotency_key_prevents_duplicate_job_creation():
    try:
        client = _client_as("user-idempotency")
        headers = {"Idempotency-Key": "same-key-123"}
        first = client.post("/api/design/generate-batch", json=_batch_payload(1), headers=headers)
        second = client.post("/api/design/generate-batch", json=_batch_payload(1), headers=headers)
        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["job_id"] == second.json()["job_id"]
    finally:
        _clear_overrides()


def test_partial_failure_does_not_mark_all_variants_failed(monkeypatch):
    """A single failing variant must not fail the whole batch - patch
    `_generate_one_variant` to fail on every third call and confirm the job
    ends as partial_failure with a mix of completed/failed counts."""
    import app.module1_design.tasks as tasks_module

    call_count = {"n": 0}
    original = tasks_module._generate_one_variant

    def _flaky(**kwargs):
        call_count["n"] += 1
        if call_count["n"] % 2 == 0:
            raise RuntimeError("simulated transient generation failure")
        return original(**kwargs)

    monkeypatch.setattr(tasks_module, "_generate_one_variant", _flaky)

    from app.core.repository import get_repository
    from app.module1_design.schemas import DesignParameters

    repo = get_repository()
    experiment_id = repo.create_experiment(user_id="user-partial", name="partial-test")
    job_id = repo.create_job(
        experiment_id=experiment_id, user_id="user-partial", job_type="design_batch", requested_count=4
    )

    result = tasks_module.run_batch_generation(
        job_id=job_id,
        experiment_id=experiment_id,
        user_id="user-partial",
        base_params=DesignParameters(geometry_type="pyramid", height_m=50),
        variation_count=4,
        vary_fields=["height_m"],
        variation_range_pct=0.1,
    )
    assert result["status"] == "partial_failure"
    assert result["completed_count"] == 2
    assert result["failed_count"] == 2
