import os

import pytest

from app import comparative_service, comparative_tasks, study_router
from app.module2_simulation import router as simulation_router
from app.module3_analysis import service as analysis_service
from app.core.repository import LocalSQLiteRepository
from app.v2.repository import EvidenceRepository


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True, scope="module")
def eager_celery():
    from app.core.celery_app import celery_app
    previous = celery_app.conf.task_always_eager
    previous_propagates = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield
    celery_app.conf.task_always_eager = previous
    celery_app.conf.task_eager_propagates = previous_propagates


@pytest.fixture
def comparative_study(tmp_path, monkeypatch):
    path = tmp_path / "comparative.sqlite3"
    repo = LocalSQLiteRepository(path)
    monkeypatch.setattr(study_router, "get_repository", lambda: repo)
    monkeypatch.setattr(study_router, "EvidenceRepository", lambda: EvidenceRepository(path))
    monkeypatch.setattr(comparative_service, "get_repository", lambda: repo)
    monkeypatch.setattr(comparative_tasks, "get_repository", lambda: repo)
    monkeypatch.setattr(simulation_router, "get_repository", lambda: repo)
    monkeypatch.setattr(analysis_service, "get_repository", lambda: repo)
    study_id = repo.create_experiment("user-test", "Five-pyramid sweep", {
        "study": {
            "research_question": "How does height change a bounded geometry-aware thermal result?",
            "geometry_family": "pyramid",
        }
    })
    repo.update_experiment(study_id, status="active")
    design_ids = []
    for index, height in enumerate((1.0, 2.0, 3.0, 4.0, 5.0)):
        design_ids.append(repo.create_design_model(
            experiment_id=study_id, user_id="user-test", geometry_family="pyramid",
            parameters={
                "geometry_type": "pyramid", "base_length_m": 2.0, "height_m": height,
                "slope_angle_deg": 45.0, "material": "concrete",
            },
            units={"base_length_m": "m", "height_m": "m", "slope_angle_deg": "deg"},
            variation_index=index, generation_status="completed",
        ))
    return repo, study_id, design_ids


def payload(design_ids, **changes):
    value = {
        "design_ids": design_ids,
        "solver_id": "pyramid_thermal_conduction_v1",
        "material": "concrete",
        "boundary_conditions": {
            "ambient_temperature_c": 20.0,
            "prescribed_temperature_c": 20.0,
            "heat_source_w_m3": 1000.0,
        },
        "numerical_settings": {"max_iterations": 1000, "tolerance": 1e-6},
        "grid_resolution": 9,
    }
    value.update(changes)
    return value


def test_controlled_five_design_geometry_aware_study(authorized_client, comparative_study):
    repo, study_id, design_ids = comparative_study
    plan = authorized_client.post(f"/api/studies/{study_id}/comparison-plan", json=payload(design_ids))
    assert plan.status_code == 200
    body = plan.json()
    assert body["evaluation_class"] == "geometry_aware_model"
    assert body["variant_count"] == 5
    assert "height_m" in body["varies"]
    assert body["held_constant"]["material"] == "concrete"
    assert body["held_constant"]["grid_resolution"] == 9

    started = authorized_client.post(f"/api/studies/{study_id}/comparative-runs", json=payload(design_ids))
    assert started.status_code == 202
    job = repo.get_job(started.json()["job_id"])
    assert job.status == "completed"
    assert job.completed_count == 5

    simulations = repo.list_simulation_jobs_for_experiment(study_id)
    assert len(simulations) == 5
    assert {item.design_id for item in simulations} == set(design_ids)
    snapshots = [repo.get_simulation_input(item.id) for item in simulations]
    assert {snapshot.geometry["height_m"] for snapshot in snapshots} == {1, 2, 3, 4, 5}
    assert len({str(snapshot.boundary_conditions) for snapshot in snapshots}) == 1
    results = [repo.get_simulation_result(item.id) for item in simulations]
    assert all(result.converged for result in results)
    assert len({round(result.summary_metrics["integrated_heat_source_w"], 8) for result in results}) == 5

    analyses = repo.list_analyses_for_experiment(study_id)
    assert len(analyses) == 1
    assert analyses[0].data_quality["valid_row_count"] == 5
    for fmt in ("json", "csv"):
        exported_simulation = authorized_client.get(
            f"/api/simulations/{simulations[0].id}/export/{fmt}"
        )
        assert exported_simulation.status_code == 200
        exported_analysis = authorized_client.get(f"/api/analyze/{analyses[0].id}/export/{fmt}")
        assert exported_analysis.status_code == 200


def test_invalid_geometry_is_blocked_before_any_simulation_record(authorized_client, comparative_study):
    repo, study_id, design_ids = comparative_study
    response = authorized_client.post(
        f"/api/studies/{study_id}/comparative-runs",
        json=payload(design_ids, grid_resolution=10),
    )
    assert response.status_code == 422
    assert repo.list_simulation_jobs_for_experiment(study_id) == []


def test_invalid_material_is_a_safe_422_without_partial_records(authorized_client, comparative_study):
    repo, study_id, design_ids = comparative_study
    response = authorized_client.post(
        f"/api/studies/{study_id}/comparative-runs",
        json=payload(design_ids, material="unobtainium"),
    )
    assert response.status_code == 422
    assert "not in the material library" in response.json()["detail"]
    assert repo.list_simulation_jobs_for_experiment(study_id) == []
