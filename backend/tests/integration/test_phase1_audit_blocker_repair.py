from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from app import study_router
from app.core.repository import LocalSQLiteRepository, SimulationResultRecord
from app.core.storage import LocalFileStorage
from app.module2_simulation.tasks import run_simulation_job
from app.module3_analysis.schemas import AnalysisCreateRequest, ObjectiveSpec
from app.module3_analysis.service import run_experiment_analysis
from app.v2 import account_router, decision_output_router
from app.v2.repository import EvidenceRepository


pytestmark = pytest.mark.integration


def _contains_private_key(value) -> bool:
    if isinstance(value, dict):
        return any(
            key in {"object_key", "storage_object_key", "result_object_keys"}
            or _contains_private_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_private_key(item) for item in value)
    return False


@pytest.fixture
def repair_stack(tmp_path, monkeypatch):
    db_path = tmp_path / "phase1-repair.db"
    storage = LocalFileStorage(tmp_path / "objects")
    monkeypatch.setenv("LOCAL_PERSISTENCE_DB_PATH", str(db_path))
    repo = LocalSQLiteRepository(db_path)
    monkeypatch.setattr(decision_output_router, "ReportService", lambda: __import__(
        "app.v2.reasoning_reports", fromlist=["ReportService"]
    ).ReportService(EvidenceRepository(repository=repo), storage, repo))
    monkeypatch.setattr(study_router, "get_repository", lambda: repo)
    monkeypatch.setattr(study_router, "EvidenceRepository", lambda: EvidenceRepository(repository=repo))
    return repo, storage


def _real_simulation(repo, storage, owner, experiment, design, solver="thermal_conduction_v1"):
    simulation = repo.create_simulation_job(owner, solver, experiment, design)
    if solver == "thermal_conduction_v1":
        parameters = {
            "material_name": "steel",
            "geometry": {"dimension": "1d", "length_m": 1.0, "num_elements": 10},
            "boundary_conditions": {
                "ambient_temperature_c": 100.0, "prescribed_temperature_c": 20.0,
            },
            "numerical_settings": {"max_iterations": 300, "tolerance": 1e-5},
        }
    else:
        parameters = {
            "material_name": "steel", "geometry": {"dimension": "1d"},
            "boundary_conditions": {"point_mass_kg": 2.0, "spring_stiffness_n_m": 200.0},
            "numerical_settings": {},
        }
    outcome = run_simulation_job(
        simulation_id=simulation, solver_id=solver, experiment_id=experiment, design_id=design,
        initial_conditions={}, repository=repo, storage=storage, **parameters,
    )
    assert outcome["status"] == "completed"
    evidence = EvidenceRepository(repository=repo)
    numerical = next(
        item for item in evidence.list(owner, "scientific_numerical_result", experiment_id=experiment)
        if item["simulation_id"] == simulation
    )
    return simulation, numerical, repo.get_simulation_result(simulation)


def _decision_payload(experiment, design, metric, value, evidence_ids):
    return {
        "experiment_id": experiment,
        "designs": [{
            "design_id": design, "metrics": {metric: value}, "parameters": {},
            "confidence": "high", "validity_status": "valid", "evidence_ids": evidence_ids,
        }],
        "objectives": [{
            "objective_id": "objective", "metric_code": metric, "direction": "minimize",
            "weight": 1.0, "unit": "degC" if metric == "max_temperature_c" else "Hz",
            "enabled": True,
        }],
        "constraints": [],
    }


def _classification(client, decision_id, experiment):
    response = client.post("/api/v2/reasoning", json={
        "experiment_id": experiment, "stage": "recommendation_produced",
        "level": "research", "evidence_ids": [decision_id],
    })
    assert response.status_code == 201, response.text
    return response.json()["payload"]["snapshot"]["facts"][0]["classification"]


def test_public_decision_exploit_metric_value_and_relationship_binding(
    authorized_client, repair_stack,
):
    repo, storage = repair_stack
    owner = "user-test"
    experiment = repo.create_experiment(owner, "binding")
    correct_design = repo.create_design_model(experiment, owner, "cube", {}, {}, 0, "completed")
    other_design = repo.create_design_model(experiment, owner, "cube", {}, {}, 1, "completed")
    wrong_experiment = repo.create_experiment(owner, "wrong experiment")
    wrong_experiment_design = repo.create_design_model(
        wrong_experiment, owner, "cube", {}, {}, 0, "completed",
    )
    _, numerical, result = _real_simulation(repo, storage, owner, experiment, correct_design)
    _, modal, _ = _real_simulation(repo, storage, owner, experiment, correct_design, "modal_eigen_1d_v1")
    _, wrong_design_evidence, _ = _real_simulation(repo, storage, owner, experiment, other_design)
    _, wrong_experiment_evidence, _ = _real_simulation(
        repo, storage, owner, wrong_experiment, wrong_experiment_design,
    )
    value = result.summary_metrics["max_temperature_c"]

    cases = [
        (value, [numerical["id"]], "finding"),
        (value + 1.0, [numerical["id"]], "insufficient_evidence"),
        (-999999.0, [modal["id"]], "insufficient_evidence"),
        (value, [wrong_design_evidence["id"]], "insufficient_evidence"),
        (value, [wrong_experiment_evidence["id"]], "insufficient_evidence"),
    ]
    for asserted, evidence_ids, expected in cases:
        response = authorized_client.post(
            "/api/v2/decisions",
            json=_decision_payload(experiment, correct_design, "max_temperature_c", asserted, evidence_ids),
        )
        assert response.status_code == 201, response.text
        assert _classification(authorized_client, response.json()["id"], experiment) == expected

    wrong_metric = _decision_payload(
        experiment, correct_design, "natural_frequency_hz", 1.0, [numerical["id"]],
    )
    response = authorized_client.post("/api/v2/decisions", json=wrong_metric)
    assert response.status_code == 201
    assert _classification(authorized_client, response.json()["id"], experiment) == "insufficient_evidence"


def test_public_decision_analysis_binding_and_cross_owner_rejection(
    authorized_client, repair_stack,
):
    repo, storage = repair_stack
    owner = "user-test"
    experiment = repo.create_experiment(owner, "analysis binding")
    designs = []
    for index in range(3):
        design = repo.create_design_model(experiment, owner, "cube", {}, {}, index, "completed")
        _real_simulation(repo, storage, owner, experiment, design)
        designs.append(design)
    analysis = run_experiment_analysis(
        experiment, owner,
        AnalysisCreateRequest(objectives=[
            ObjectiveSpec(column="metric.max_temperature_c", direction="minimize", weight=1),
        ]), repo,
    )
    row = next(
        item for item in analysis.result["dataset"]["rows"] if item["design_id"] == designs[0]
    )
    value = row["values"]["metric.max_temperature_c"]
    response = authorized_client.post(
        "/api/v2/decisions",
        json=_decision_payload(
            experiment, designs[0], "max_temperature_c", value, [analysis.analysis_evidence_id],
        ),
    )
    assert response.status_code == 201
    assert _classification(authorized_client, response.json()["id"], experiment) == "finding"

    bad = authorized_client.post(
        "/api/v2/decisions",
        json=_decision_payload(
            experiment, designs[0], "max_temperature_c", value + 1, [analysis.analysis_evidence_id],
        ),
    )
    assert _classification(authorized_client, bad.json()["id"], experiment) == "insufficient_evidence"

    from app.core.auth import get_current_user
    from app.main import app
    app.dependency_overrides[get_current_user] = lambda: {"id": "other-owner", "role": "researcher"}
    try:
        denied = authorized_client.post(
            "/api/v2/decisions",
            json=_decision_payload(
                experiment, designs[0], "max_temperature_c", value, [analysis.analysis_evidence_id],
            ),
        )
        assert denied.status_code == 201
        assert _classification(authorized_client, denied.json()["id"], experiment) == "insufficient_evidence"
    finally:
        app.dependency_overrides[get_current_user] = lambda: {"id": owner, "role": "researcher"}

    analysis_record = repo.get_analysis(analysis.id)
    tampered_result = dict(analysis_record.result)
    tampered_dataset = dict(tampered_result["dataset"])
    tampered_rows = [dict(item) for item in tampered_dataset["rows"]]
    tampered_rows[0] = {**tampered_rows[0], "values": {
        **tampered_rows[0]["values"], "metric.max_temperature_c": value + 1000,
    }}
    tampered_dataset["rows"] = tampered_rows
    tampered_result["dataset"] = tampered_dataset
    with sqlite3.connect(repo.db_path) as database:
        database.execute(
            "update experiment_analyses set result=? where id=?", (json.dumps(tampered_result), analysis.id),
        )
    mismatched = authorized_client.post(
        "/api/v2/decisions",
        json=_decision_payload(
            experiment, designs[0], "max_temperature_c", value, [analysis.analysis_evidence_id],
        ),
    )
    assert _classification(authorized_client, mismatched.json()["id"], experiment) == "insufficient_evidence"


def test_public_report_surfaces_hide_keys_but_internal_download_works(
    authorized_client, repair_stack,
):
    repo, _ = repair_stack
    evidence = EvidenceRepository(repository=repo)
    owner = "user-test"
    experiment = repo.create_experiment(owner, "report privacy")
    source = evidence.create(owner, {
        "record_type": "run_manifest", "status": "completed", "experiment_id": experiment,
        "simulation_id": None, "parent_record_id": None,
        "payload": {"status": "completed", "nested": {"object_key": "must-not-leak"}},
    })
    created = authorized_client.post("/api/v2/reports", json={
        "experiment_id": experiment, "title": "Private artifacts", "evidence_ids": [source["id"]],
    })
    assert created.status_code == 201, created.text
    report_id = created.json()["id"]
    assert not _contains_private_key(created.json())
    assert not _contains_private_key(authorized_client.get(f"/api/v2/reports/{report_id}").json())
    assert not _contains_private_key(authorized_client.get(f"/api/v2/reports/{report_id}/artifacts").json())
    assert not _contains_private_key(authorized_client.get("/api/v2/reports").json())
    assert not _contains_private_key(authorized_client.get("/api/v2/dashboard").json())
    assert not _contains_private_key(authorized_client.get(f"/api/studies/{experiment}").json())
    assert not _contains_private_key(authorized_client.get(f"/api/v2/evidence/{report_id}").json())

    persisted = evidence.get(report_id, owner)
    assert persisted["payload"]["artifacts"][0]["object_key"].startswith("users/")
    downloaded = authorized_client.get(f"/api/v2/reports/{report_id}/exports/json")
    assert downloaded.status_code == 200
    assert hashlib.sha256(downloaded.content).hexdigest() == persisted["payload"]["artifacts"][0]["checksum_sha256"]
    assert b"object_key" not in downloaded.content and b"must-not-leak" not in downloaded.content

    from app.core.auth import get_current_user
    from app.main import app
    app.dependency_overrides[get_current_user] = lambda: {"id": "other-owner", "role": "researcher"}
    try:
        assert authorized_client.get(f"/api/v2/reports/{report_id}").status_code == 404
        assert authorized_client.get(f"/api/v2/reports/{report_id}/exports/json").status_code == 404
    finally:
        app.dependency_overrides[get_current_user] = lambda: {"id": owner, "role": "researcher"}

    serialized = json.dumps(created.json())
    assert "object_key" not in serialized and "must-not-leak" not in serialized


def test_public_execution_records_hide_private_storage_keys(
    authorized_client, repair_stack,
):
    repo, _ = repair_stack
    evidence = EvidenceRepository(repository=repo)
    manifest = evidence.create("user-test", {
        "record_type": "run_manifest", "status": "sealed", "experiment_id": "experiment",
        "simulation_id": None, "parent_record_id": None,
        "payload": {
            "manifest_id": "manifest-private", "status": "sealed",
            "artifacts": [{"object_key": "users/user-test/experiments/experiment/simulations/run/private.json"}],
            "bundle": {"object_key": "users/user-test/experiments/experiment/simulations/run/bundle.json"},
        },
    })
    retrieved = authorized_client.get(f"/api/v2/execution/manifests/{manifest['id']}")
    artifacts = authorized_client.get(f"/api/v2/execution/manifests/{manifest['id']}/artifacts")
    assert retrieved.status_code == artifacts.status_code == 200
    assert not _contains_private_key(retrieved.json())
    assert not _contains_private_key(artifacts.json())
    assert _contains_private_key(evidence.get(manifest["id"], "user-test"))
