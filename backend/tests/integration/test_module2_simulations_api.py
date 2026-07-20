"""
End-to-end ownership/authorization and error-handling proof for the new
unified `/api/simulations/*` API (Phase C8), driven through the real
FastAPI app over `TestClient` with Celery in eager mode (see
`tests/integration/test_batch_jobs.py` for the same pattern applied to
Module 1 batch jobs).

Covers:
- Full happy-path lifecycle (create -> poll status -> fetch results) for a
  real solver (`thermal_conduction_v1`).
- Ownership enforcement: a different authenticated user cannot see/cancel/
  fetch-results of someone else's simulation (404, not 403 - fail closed
  without confirming existence).
- Rejection of prototype/planned solvers (422) - the API must never return
  a fabricated result for an unimplemented solver.
- Rejection of an unknown material (422).
- Idempotency-Key replay returns the same simulation_id instead of creating
  a duplicate job.
- Per-user concurrent job rate limiting (429).
"""
import os

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True, scope="module")
def _eager_celery():
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


def _thermal_payload(**overrides):
    payload = {
        "solver_id": "thermal_conduction_v1",
        "material": {"name": "steel"},
        "geometry": {"dimension": "1d", "length_m": 1.0, "num_elements": 10},
        "boundary_conditions": {"ambient_temperature_c": 100.0, "prescribed_temperature_c": 20.0},
    }
    payload.update(overrides)
    return payload


def test_full_lifecycle_create_status_results():
    from app.main import app

    try:
        client = _client_as("user-lifecycle")
        create_response = client.post("/api/simulations", json=_thermal_payload())
        assert create_response.status_code == 202
        body = create_response.json()
        simulation_id = body["simulation_id"]
        assert body["status"] in {"queued", "running", "completed"}

        status_response = client.get(f"/api/simulations/{simulation_id}")
        assert status_response.status_code == 200
        assert status_response.json()["status"] == "completed"

        results_response = client.get(f"/api/simulations/{simulation_id}/results")
        assert results_response.status_code == 200
        results_body = results_response.json()
        assert results_body["status"] == "completed"
        assert results_body["result"] is not None
        assert results_body["result"]["solver_id"] == "thermal_conduction_v1"
        assert results_body["result"]["convergence"]["converged"] is True
    finally:
        app.dependency_overrides.clear()


def test_other_user_cannot_see_cancel_or_fetch_results():
    from app.main import app

    try:
        owner_client = _client_as("user-owner")
        create_response = owner_client.post("/api/simulations", json=_thermal_payload())
        assert create_response.status_code == 202
        simulation_id = create_response.json()["simulation_id"]

        other_client = _client_as("user-other")
        assert other_client.get(f"/api/simulations/{simulation_id}").status_code == 404
        assert other_client.post(f"/api/simulations/{simulation_id}/cancel").status_code == 404
        assert other_client.get(f"/api/simulations/{simulation_id}/results").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_unknown_simulation_id_is_404():
    from app.main import app

    try:
        client = _client_as("user-unknown-id")
        response = client.get("/api/simulations/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_prototype_solver_is_rejected_not_silently_run():
    from app.main import app

    try:
        client = _client_as("user-unsupported")
        payload = _thermal_payload(
            solver_id="cfd_wind_drag_v1",
            geometry={"dimension": "3d", "grid_resolution": 10},
        )
        response = client.post("/api/simulations", json=payload)
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_unknown_material_is_rejected():
    from app.main import app

    try:
        client = _client_as("user-bad-material")
        payload = _thermal_payload(material={"name": "unobtainium"})
        response = client.post("/api/simulations", json=payload)
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_idempotency_key_replay_returns_same_simulation_id():
    from app.main import app

    try:
        client = _client_as("user-idempotent")
        headers = {"Idempotency-Key": "same-key-123"}
        first = client.post("/api/simulations", json=_thermal_payload(), headers=headers)
        second = client.post("/api/simulations", json=_thermal_payload(), headers=headers)
        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["simulation_id"] == second.json()["simulation_id"]
    finally:
        app.dependency_overrides.clear()


def test_rate_limit_blocks_excess_concurrent_jobs(monkeypatch):
    """Jobs must stay 'queued' (not yet completed) for the active-job count
    to accumulate, so the actual Celery dispatch is stubbed out for this
    test only - otherwise every job would finish synchronously (eager mode)
    before the next request is even sent, and the active count would never
    exceed zero; a live broker is also not available in this environment."""
    from app.module2_simulation import tasks as module2_tasks
    from app.core.config import settings
    from app.main import app

    monkeypatch.setattr(module2_tasks.run_simulation_job_task, "delay", lambda **kwargs: None)
    try:
        client = _client_as("user-rate-limited")
        limit = settings.MAX_CONCURRENT_SIMULATION_JOBS_PER_USER
        statuses = []
        for i in range(limit + 2):
            response = client.post(
                "/api/simulations",
                json=_thermal_payload(),
                headers={"Idempotency-Key": f"rate-limit-key-{i}"},
            )
            statuses.append(response.status_code)
        assert 429 in statuses
    finally:
        app.dependency_overrides.clear()
