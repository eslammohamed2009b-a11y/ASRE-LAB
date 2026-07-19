import pytest

from app import pipeline_router
from app.module2_simulation import router as simulation_router

pytestmark = pytest.mark.integration


class _FakeTask:
    def __init__(self, task_id: str):
        self.id = task_id


class _FakeAsyncResult:
    def __init__(self, status: str = "SUCCESS", result=None):
        self.status = status
        self.result = result if result is not None else {"ok": True}

    def successful(self) -> bool:
        return self.status == "SUCCESS"

    def failed(self) -> bool:
        return self.status == "FAILURE"


def test_auth_guard_blocks_without_token(client):
    response = client.post("/api/simulate/advisor", json={"model_type": "pyramid"})
    assert response.status_code == 401


def test_advisor_with_authorized_client(authorized_client):
    response = authorized_client.post("/api/simulate/advisor", json={"model_type": "pyramid"})
    assert response.status_code == 200
    payload = response.json()
    assert "recommended" in payload


def test_pipeline_run_sync(authorized_client, monkeypatch):
    def _fake_run_pipeline_flow(prompt: str, variation_count: int, analyses, user_id: str):
        return {
            "experiment_id": "exp-1",
            "base_params": {"geometry_type": "pyramid"},
            "generated_count": variation_count,
            "analyzed_count": variation_count,
            "clusters": {},
            "correlation": {},
            "insights": {},
            "persistence_enabled": False,
            "user_id": user_id,
        }

    monkeypatch.setattr(pipeline_router, "run_pipeline_flow", _fake_run_pipeline_flow)

    response = authorized_client.post(
        "/api/pipeline/run",
        json={
            "prompt": "pyramid 146 m",
            "variation_count": 3,
            "analyses": ["thermal"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["generated_count"] == 3
    assert payload["user_id"] == "user-test"


def test_simulation_async_and_status(authorized_client, monkeypatch):
    monkeypatch.setattr(simulation_router.run_simulation_task, "delay", lambda payload: _FakeTask("job-123"))
    monkeypatch.setattr(simulation_router, "AsyncResult", lambda _: _FakeAsyncResult())

    enqueue = authorized_client.post(
        "/api/simulate/run-async",
        json={
            "design_id": "d-1",
            "geometry_type": "tower",
            "analysis_type": "thermal",
            "material": "concrete",
            "boundary_conditions": {},
        },
    )
    assert enqueue.status_code == 200
    assert enqueue.json()["job_id"] == "job-123"

    status = authorized_client.get("/api/simulate/jobs/job-123")
    assert status.status_code == 200
    assert status.json()["status"] == "success"
