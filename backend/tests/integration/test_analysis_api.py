import pytest
import numpy as np

from app.core.repository import LocalSQLiteRepository, SimulationResultRecord
from app.core.storage import LocalFileStorage
from app.module2_simulation.field_results import persist_field_result

pytestmark = pytest.mark.integration


def _seed(repo: LocalSQLiteRepository, storage: LocalFileStorage):
    experiment_id = repo.create_experiment("user-test", "API analysis")
    for index in range(1, 6):
        design_id = repo.create_design_model(
            experiment_id, "user-test", "beam", {"width": float(index)}, {"width": "m"}, index,
        )
        simulation_id = repo.create_simulation_job(
            "user-test", "structural_linear_static", experiment_id, design_id,
        )
        repo.record_simulation_input(
            simulation_id, "steel", {"density": 7800.0}, {"density": "kg/m^3"}, {}, {}, {},
        )
        repo.record_simulation_result(SimulationResultRecord(
            simulation_id=simulation_id, solver_id="structural_linear_static", solver_version="1.0",
            converged=True, summary_metrics={"strength_pa": index * 10.0, "mass_kg": 10.0 - index},
        ))
        repo.update_simulation_job(simulation_id, status="completed", progress_percent=100)
        if index == 1:
            persist_field_result(
                repository=repo, storage=storage, user_id="user-test", experiment_id=experiment_id,
                simulation_id=simulation_id, variable_name="axial_displacement", unit="m",
                axes=[{"name": "x", "unit": "m", "values": [0.0, 0.5, 1.0]}],
                values=np.array([0.0, 1e-6, 2e-6]), solver_id="structural_linear_static",
                solver_version="1.0", grid_metadata={"converged": True, "assumptions": ["linear elastic"]},
            )
    return experiment_id


def test_analysis_api_persists_lists_retrieves_and_hides_other_owner(
    authorized_client, monkeypatch, tmp_path,
):
    db_path = tmp_path / "api-analysis.db"
    monkeypatch.setenv("LOCAL_PERSISTENCE_DB_PATH", str(db_path))
    repo = LocalSQLiteRepository(db_path)
    experiment_id = _seed(repo, LocalFileStorage(tmp_path / "field-objects"))
    assert len(repo.list_field_results(repo.list_simulation_jobs_for_experiment(experiment_id)[0].id)) == 1

    created = authorized_client.post(
        f"/api/analyze/experiments/{experiment_id}",
        json={
            "correlation_method": "both",
            "sensitivity": {"target": "metric.strength_pa", "features": ["design.width"]},
            "objectives": [
                {"column": "metric.strength_pa", "direction": "maximize", "weight": 2},
                {"column": "metric.mass_kg", "direction": "minimize", "weight": 1},
            ],
        },
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["status"] == "completed"
    assert len(payload["dataset_hash"]) == 64
    assert "field.axial_displacement.maximum" in payload["result"]["dataset"]["columns"]
    assert payload["result"]["recommendations"][0]["evidence"]["source_ids"]

    listed = authorized_client.get(f"/api/analyze/experiments/{experiment_id}")
    retrieved = authorized_client.get(f"/api/analyze/{payload['id']}")
    assert listed.status_code == 200 and [item["id"] for item in listed.json()] == [payload["id"]]
    assert retrieved.status_code == 200 and retrieved.json()["dataset_hash"] == payload["dataset_hash"]
    quality = authorized_client.get(f"/api/analyze/{payload['id']}/results/data_quality")
    ranking = authorized_client.get(f"/api/analyze/{payload['id']}/results/ranking")
    assert quality.status_code == 200 and quality.json()["valid_row_count"] == 5
    assert ranking.status_code == 200 and ranking.json()["ranking"][0]["rank"] == 1

    invalid = authorized_client.post(
        f"/api/analyze/experiments/{experiment_id}",
        json={"sensitivity": {"target": "unknown", "features": ["design.width"]}},
    )
    assert invalid.status_code == 422

    from app.core.auth import get_current_user
    from app.main import app
    app.dependency_overrides[get_current_user] = lambda: {"id": "user-b", "role": "researcher"}
    try:
        assert authorized_client.get(f"/api/analyze/{payload['id']}").status_code == 404
        assert authorized_client.get(f"/api/analyze/experiments/{experiment_id}").status_code == 404
    finally:
        app.dependency_overrides[get_current_user] = lambda: {"id": "user-test", "role": "researcher"}
