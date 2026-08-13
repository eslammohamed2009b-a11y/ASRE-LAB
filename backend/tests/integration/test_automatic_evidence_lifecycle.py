import json

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.repository import LocalSQLiteRepository, SimulationResultRecord
from app.core.storage import LocalFileStorage
from app.main import app
from app.module2_simulation import service, tasks
from app.module2_simulation.evidence_lifecycle import persist_automatic_evidence
from app.v2.repository import EvidenceRepository

pytestmark = pytest.mark.integration


def _run(tmp_path, solver_id="thermal_conduction_v1", geometry=None, boundary=None, material="steel"):
    repo = LocalSQLiteRepository(tmp_path / f"{solver_id}.db")
    storage = LocalFileStorage(tmp_path / f"{solver_id}-objects")
    simulation_id = repo.create_simulation_job("owner-a", solver_id)
    outcome = tasks.run_simulation_job(
        simulation_id=simulation_id, solver_id=solver_id, material_name=material,
        geometry=geometry or {"dimension": "1d", "length_m": 1.0, "num_elements": 10},
        boundary_conditions=boundary or {"ambient_temperature_c": 100.0, "prescribed_temperature_c": 20.0},
        initial_conditions={}, numerical_settings={"max_iterations": 300, "tolerance": 1e-5},
        repository=repo, storage=storage,
    )
    return repo, storage, simulation_id, outcome


def _records(repo, simulation_id):
    return EvidenceRepository(repository=repo).list_scientific_for_simulation("owner-a", simulation_id)


def test_real_field_solver_automatically_creates_complete_authoritative_evidence(tmp_path):
    repo, _, simulation_id, outcome = _run(tmp_path)
    assert outcome["status"] == "completed"
    records = _records(repo, simulation_id)
    types = [record["record_type"] for record in records]
    assert types.count("scientific_numerical_result") == 1
    assert types.count("scientific_field_result") == 1
    assert types.count("scientific_validity") == 1
    assert types.count("scientific_run_convergence") == 1

    result = repo.get_simulation_result(simulation_id)
    numerical = next(record for record in records if record["record_type"] == "scientific_numerical_result")
    assert numerical["payload"]["solver_id"] == result.solver_id
    assert numerical["payload"]["solver_version"] == result.solver_version
    assert numerical["payload"]["input_fingerprint"] == result.validation_metadata["input_fingerprint"]
    assert numerical["payload"]["result_hash"] == result.reproducibility_hash
    field = repo.list_field_results(simulation_id)[0]
    field_evidence = next(record for record in records if record["record_type"] == "scientific_field_result")
    assert field_evidence["payload"]["checksum_sha256"] == field.checksum_sha256
    assert field_evidence["payload"]["source_ids"] == [numerical["id"]]


def test_scalar_direct_solver_does_not_fabricate_field_or_iterative_convergence(tmp_path):
    repo, _, simulation_id, outcome = _run(
        tmp_path, solver_id="modal_eigen_1d_v1", geometry={"dimension": "1d"},
        boundary={"point_mass_kg": 2.0, "spring_stiffness_n_m": 200.0},
    )
    assert outcome["status"] == "completed"
    records = _records(repo, simulation_id)
    assert not any(record["record_type"] == "scientific_field_result" for record in records)
    convergence = next(record for record in records if record["record_type"] == "scientific_run_convergence")
    assert convergence["status"] == "not_applicable"
    assert convergence["payload"]["metric_type"] == "direct_solver"
    assert convergence["payload"]["passed"] is None


def test_iterative_thermal_convergence_preserves_maximum_update_semantics(tmp_path):
    repo, _, simulation_id, outcome = _run(
        tmp_path, geometry={"dimension": "3d", "grid_resolution": 5},
        boundary={"ambient_temperature_c": 20.0, "heat_source_w_m3": 100.0},
    )
    assert outcome["status"] == "completed"
    convergence = next(
        record for record in _records(repo, simulation_id)
        if record["record_type"] == "scientific_run_convergence"
    )
    assert convergence["payload"]["metric_type"] == "maximum_iteration_update"
    assert convergence["payload"]["metric_value"] is not None
    assert convergence["payload"]["tolerance"] == pytest.approx(1e-5)


def test_partially_validated_solver_and_real_warnings_do_not_become_unqualified_valid(tmp_path):
    repo, _, simulation_id, outcome = _run(
        tmp_path, solver_id="pyramid_thermal_conduction_v1", material="concrete",
        geometry={"dimension": "pyramid3d", "base_length_m": 2.0, "height_m": 2.0, "grid_resolution": 9},
        boundary={"ambient_temperature_c": 20.0, "prescribed_temperature_c": 20.0, "heat_source_w_m3": 100.0},
    )
    assert outcome["status"] == "completed"
    validity = next(
        record for record in _records(repo, simulation_id)
        if record["record_type"] == "scientific_validity"
    )
    assert validity["status"] == "valid_with_warnings"
    assert any(rule["rule_id"] == "solver_validation_scope" for rule in validity["payload"]["rules"])


def test_failed_validation_does_not_create_false_success_evidence(tmp_path):
    repo, _, simulation_id, outcome = _run(
        tmp_path, geometry={"dimension": "3d", "grid_resolution": 4},
        boundary={"ambient_temperature_c": 20.0},
    )
    assert outcome["status"] == "failed"
    assert repo.get_simulation_result(simulation_id) is None
    assert _records(repo, simulation_id) == []


def test_field_persistence_failure_creates_no_result_or_completed_evidence(tmp_path):
    class FailingStorage(LocalFileStorage):
        def save_file(self, object_key, source_path):
            raise RuntimeError("storage unavailable")

    repo = LocalSQLiteRepository(tmp_path / "field-failure.db")
    simulation_id = repo.create_simulation_job("owner-a", "thermal_conduction_v1")
    outcome = tasks.run_simulation_job(
        simulation_id=simulation_id, solver_id="thermal_conduction_v1", material_name="steel",
        geometry={"dimension": "1d", "length_m": 1.0, "num_elements": 10},
        boundary_conditions={"ambient_temperature_c": 100.0, "prescribed_temperature_c": 20.0},
        initial_conditions={}, numerical_settings={"max_iterations": 300, "tolerance": 1e-5},
        repository=repo, storage=FailingStorage(tmp_path / "objects"),
    )
    assert outcome["status"] == "partial_failure"
    assert repo.get_simulation_result(simulation_id) is None
    assert _records(repo, simulation_id) == []


def test_result_persistence_failure_cannot_create_authoritative_evidence(tmp_path):
    class FailingResultRepository(LocalSQLiteRepository):
        def record_simulation_result(self, result):
            raise RuntimeError("result database unavailable")

    repo = FailingResultRepository(tmp_path / "result-failure.db")
    simulation_id = repo.create_simulation_job("owner-a", "thermal_conduction_v1")
    outcome = tasks.run_simulation_job(
        simulation_id=simulation_id, solver_id="thermal_conduction_v1", material_name="steel",
        geometry={"dimension": "1d", "length_m": 1.0, "num_elements": 10},
        boundary_conditions={"ambient_temperature_c": 100.0, "prescribed_temperature_c": 20.0},
        initial_conditions={}, numerical_settings={"max_iterations": 300, "tolerance": 1e-5},
        repository=repo, storage=LocalFileStorage(tmp_path / "objects"),
    )
    assert outcome["status"] == "partial_failure"
    assert repo.get_simulation_result(simulation_id) is None
    assert _records(repo, simulation_id) == []


def test_worker_retry_is_idempotent_and_does_not_duplicate_fields_or_evidence(tmp_path):
    repo, storage, simulation_id, first = _run(tmp_path)
    fields_before = repo.list_field_results(simulation_id)
    records_before = _records(repo, simulation_id)
    second = tasks.run_simulation_job(
        simulation_id=simulation_id, solver_id="thermal_conduction_v1", material_name="steel",
        geometry={"dimension": "1d", "length_m": 1.0, "num_elements": 10},
        boundary_conditions={"ambient_temperature_c": 100.0, "prescribed_temperature_c": 20.0},
        initial_conditions={}, numerical_settings={"max_iterations": 300, "tolerance": 1e-5},
        repository=repo, storage=storage,
    )
    assert first["status"] == second["status"] == "completed"
    assert [field.id for field in repo.list_field_results(simulation_id)] == [field.id for field in fields_before]
    assert [record["id"] for record in _records(repo, simulation_id)] == [record["id"] for record in records_before]


def test_malformed_or_missing_persisted_provenance_fails_safely(tmp_path):
    repo = LocalSQLiteRepository(tmp_path / "malformed.db")
    simulation_id = repo.create_simulation_job("owner-a", "thermal_conduction_v1")
    repo.record_simulation_input(simulation_id, "steel", {}, {}, {}, {}, {}, {})
    repo.record_simulation_result(SimulationResultRecord(
        simulation_id=simulation_id, solver_id="thermal_conduction_v1", solver_version="1.0.0",
        summary_metrics={"temperature_c": 50.0}, status="completed",
    ))
    with pytest.raises(ValueError, match="provenance is incomplete"):
        persist_automatic_evidence(repo, simulation_id)
    assert _records(repo, simulation_id) == []


def test_missing_convergence_information_never_becomes_completed_convergence(tmp_path):
    repo = LocalSQLiteRepository(tmp_path / "missing-convergence.db")
    simulation_id = repo.create_simulation_job("owner-a", "thermal_conduction_v1")
    repo.update_simulation_job(simulation_id, status="completed")
    repo.record_simulation_input(simulation_id, "steel", {"k": 50.0}, {}, {}, {}, {}, {})
    repo.record_simulation_result(SimulationResultRecord(
        simulation_id=simulation_id, solver_id="thermal_conduction_v1", solver_version="1.0.0",
        summary_metrics={"temperature_c": 50.0}, status="completed",
        validation_metadata={
            "input_fingerprint": "a" * 64, "material_properties_used": {"k": 50.0},
            "validation_status": "validated",
        },
        reproducibility_hash="b" * 64,
    ))
    persist_automatic_evidence(repo, simulation_id)
    convergence = next(
        record for record in _records(repo, simulation_id)
        if record["record_type"] == "scientific_run_convergence"
    )
    assert convergence["status"] == "not_run"
    assert convergence["payload"]["passed"] is None


def test_owner_scoped_retrieval_is_typed_traceable_and_leaks_no_storage_location(tmp_path, monkeypatch):
    repo, _, simulation_id, _ = _run(tmp_path)
    monkeypatch.setattr(service, "get_repository", lambda: repo)
    app.dependency_overrides[get_current_user] = lambda: {"id": "owner-a"}
    client = TestClient(app)
    response = client.get(f"/api/simulations/{simulation_id}/evidence")
    assert response.status_code == 200
    body = response.json()
    assert {record["record_type"] for record in body} >= {
        "scientific_numerical_result", "scientific_validity", "scientific_run_convergence"
    }
    serialized = json.dumps(body)
    assert "storage_object_key" not in serialized and ".npz" not in serialized
    assert all(record["simulation_id"] == simulation_id for record in body)

    app.dependency_overrides[get_current_user] = lambda: {"id": "owner-b"}
    assert client.get(f"/api/simulations/{simulation_id}/evidence").status_code == 404
    assert client.get("/api/simulations/not-real/evidence").status_code == 404
    assert client.post(f"/api/simulations/{simulation_id}/evidence", json={}).status_code == 405
    app.dependency_overrides.clear()
