import pytest

from app.core.auth import get_current_user
from app.core.repository import LocalSQLiteRepository, SimulationResultRecord
from app.main import app


@pytest.fixture
def production_chain(tmp_path, monkeypatch):
    path = tmp_path / "authoritative-benchmark.db"
    monkeypatch.setenv("LOCAL_PERSISTENCE_DB_PATH", str(path))
    repository = LocalSQLiteRepository(path)

    def create_source(
        *,
        owner="owner-a",
        solver="thermal_conduction_v1",
        job_status="completed",
        persist_result=True,
        result_status="completed",
        metrics=None,
    ):
        simulation_id = repository.create_simulation_job(owner, solver)
        repository.update_simulation_job(simulation_id, status=job_status)
        if persist_result:
            repository.record_simulation_result(SimulationResultRecord(
                simulation_id=simulation_id,
                solver_id=solver,
                solver_version="1.0.0",
                status=result_status,
                summary_metrics={"temperature_c": 50.0} if metrics is None else metrics,
            ))
        return simulation_id

    yield create_source
    app.dependency_overrides.clear()


def _authorize(owner):
    app.dependency_overrides[get_current_user] = lambda: {"id": owner, "role": "researcher"}


def _benchmark(client, simulation_id, computed=50.0):
    return client.post(
        "/api/v2/scientific/solvers/thermal_conduction_v1/benchmark",
        json={
            "inputs": {"cold_c": 0.0, "hot_c": 100.0},
            "computed_result": computed,
            "source_simulation_id": simulation_id,
        },
    )


def test_real_owner_scoped_completed_source_succeeds(client, production_chain):
    _authorize("owner-a")
    assert _benchmark(client, production_chain()).status_code == 200


def test_fake_source_simulation_id_is_rejected(client, production_chain):
    _authorize("owner-a")
    assert _benchmark(client, "not-a-real-simulation").status_code == 404


def test_cross_owner_source_is_rejected(client, production_chain):
    source = production_chain(owner="owner-a")
    _authorize("owner-b")
    assert _benchmark(client, source).status_code == 404


def test_wrong_solver_source_is_rejected(client, production_chain):
    _authorize("owner-a")
    source = production_chain(solver="structural_linear_1d_v1")
    assert _benchmark(client, source).status_code == 422


def test_incomplete_source_is_rejected(client, production_chain):
    _authorize("owner-a")
    source = production_chain(job_status="running")
    assert _benchmark(client, source).status_code == 422


def test_missing_persisted_result_is_rejected(client, production_chain):
    _authorize("owner-a")
    source = production_chain(persist_result=False)
    assert _benchmark(client, source).status_code == 422


def test_missing_benchmark_metric_is_rejected(client, production_chain):
    _authorize("owner-a")
    source = production_chain(metrics={"maximum_temperature_c": 50.0})
    assert _benchmark(client, source).status_code == 422


def test_falsified_client_computed_result_is_rejected(client, production_chain):
    _authorize("owner-a")
    source = production_chain(metrics={"temperature_c": 50.0})
    assert _benchmark(client, source, computed=49.0).status_code == 422


def test_persisted_metric_remains_authoritative(client, production_chain):
    _authorize("owner-a")
    response = _benchmark(client, production_chain(metrics={"temperature_c": 50.0}))
    assert response.status_code == 200
    assert response.json()["computed_result"] == 50.0


def test_valid_computation_has_correct_error_tolerance_and_pass_state(client, production_chain):
    _authorize("owner-a")
    response = _benchmark(client, production_chain(metrics={"temperature_c": 50.0}))
    body = response.json()
    assert body["reference_result"] == 50.0
    assert body["absolute_error"] == body["relative_error"] == 0.0
    assert body["declared_tolerance"] == pytest.approx(1e-6)
    assert body["passed"] is True


def test_reference_only_cannot_claim_pass(client, production_chain):
    _authorize("owner-a")
    response = client.post(
        "/api/v2/scientific/solvers/thermal_conduction_v1/reference-only",
        json={"inputs": {"cold_c": 0.0, "hot_c": 100.0}},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "reference_only"
    assert response.json()["passed"] is None
