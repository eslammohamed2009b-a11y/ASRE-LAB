from __future__ import annotations

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.repository import LocalSQLiteRepository
from app.core.storage import LocalFileStorage, StorageError
from app.main import app
from app.module2_simulation import geometry_physics_router as api
from app.module2_simulation import service as simulation_service
from app.module2_simulation import source_resolution
from app.module2_simulation.acoustic_benchmark import (
    BENCHMARK_ID,
    REFINEMENT_TARGETS_MM,
    benchmark_design,
    benchmark_domain,
    benchmark_mesh_specification,
    benchmark_physics_request,
)
from app.module2_simulation.acoustic_evidence import load_acoustic_fields
from app.module2_simulation.acoustic_fem_solvers import AcousticFEMError
from app.module2_simulation.cad_fem_execution import execute_cad_fem
from app.module2_simulation.geometry_physics_schemas import HarmonicAcousticSettings, PhysicsModelV1
from app.v2 import scientific_router
from app.v2.repository import EvidenceRepository


pytestmark = pytest.mark.integration
SOLVER_ID = "acoustic_helmholtz_fem_3d_v1"


def test_real_authenticated_acoustic_lifecycle_refinement_trust_privacy_and_failure(tmp_path, monkeypatch):
    repo = LocalSQLiteRepository(tmp_path / "records.db")
    storage = LocalFileStorage(tmp_path / "private")
    owner, other = "acoustic-owner-a", "acoustic-owner-b"
    experiment_id = repo.create_experiment(owner, "Final acoustic lifecycle")
    for module in (api, simulation_service, scientific_router):
        monkeypatch.setattr(module, "get_repository", lambda: repo)
    for module in (api, simulation_service, scientific_router):
        monkeypatch.setattr(module, "get_storage", lambda: storage)
    monkeypatch.setattr(source_resolution, "get_repository", lambda: repo)

    app.dependency_overrides[get_current_user] = lambda: {"id": owner, "role": "researcher"}
    try:
        client = TestClient(app)
        simulations, models = [], []
        for level, target_size_mm in REFINEMENT_TARGETS_MM.items():
            request = {
                "experiment_id": experiment_id,
                "document": benchmark_design().model_dump(mode="json"),
                "domains": [benchmark_domain().model_dump(mode="json")],
                "specification": benchmark_mesh_specification(target_size_mm).model_dump(mode="json"),
                "physics": benchmark_physics_request().model_dump(mode="json"),
            }
            created = client.post("/api/geometry-physics/physics", json=request)
            assert created.status_code == 200, created.text
            model = created.json(); models.append(model)
            executed = client.post(
                f"/api/geometry-physics/physics/{model['physics_model_id']}/execute",
                json={"solver_id": SOLVER_ID},
                headers={"Idempotency-Key": f"final-acoustic-{level}"},
            )
            assert executed.status_code == 200, executed.text
            assert executed.json()["status"] == "completed"
            simulations.append(executed.json()["simulation_id"])

        retry = client.post(
            f"/api/geometry-physics/physics/{models[-1]['physics_model_id']}/execute",
            json={"solver_id": SOLVER_ID}, headers={"Idempotency-Key": "final-acoustic-fine"},
        )
        assert retry.status_code == 200 and retry.json()["simulation_id"] == simulations[-1]

        fine = simulations[-1]
        result_response = client.get(f"/api/simulations/{fine}/results")
        assert result_response.status_code == 200
        result = result_response.json()["result"]
        assert result["solver_id"] == SOLVER_ID and result["status"] == "completed"
        metadata = result["validation_metadata"]
        assert metadata["physics_model_hash"] == models[-1]["physics_hash"]
        assert metadata["mesh_hash"] == models[-1]["mesh_hash"]
        assert metadata["k_h_max"] <= metadata["maximum_k_h"] == 0.5
        assert metadata["reciprocal_condition_estimate"] >= metadata["minimum_reciprocal_condition_estimate"]

        fields_response = client.get(f"/api/simulations/{fine}/fields")
        assert fields_response.status_code == 200
        fields = {item["variable_name"]: item for item in fields_response.json()}
        assert set(fields) == {
            "pressure_real_pa", "pressure_imag_pa", "pressure_amplitude_pa",
            "pressure_phase_rad", "sound_pressure_level_db", "sound_pressure_level_defined",
        }
        node_count = fields["pressure_real_pa"]["array_shape"][0]
        assert all(item["array_shape"] == [node_count] and item["checksum_sha256"] for item in fields.values())
        for item in fields.values():
            assert client.get(f"/api/simulations/{fine}/fields/{item['id']}").status_code == 200
            assert client.get(f"/api/simulations/{fine}/fields/{item['id']}/download").status_code == 200
        arrays, records, _, _ = load_acoustic_fields(
            repository=repo, storage=storage, user_id=owner, simulation_id=fine,
        )
        assert all(values.shape == (node_count,) and np.isfinite(values).all() for values in arrays.values())

        evidence_response = client.get(f"/api/simulations/{fine}/evidence")
        assert evidence_response.status_code == 200
        evidence = evidence_response.json()
        types = [item["record_type"] for item in evidence]
        assert types.count("scientific_numerical_result") == 1
        assert types.count("scientific_field_result") == 6
        assert types.count("scientific_validity") == 1
        assert types.count("scientific_run_convergence") == 1
        assert types.count("scientific_benchmark") == 1

        benchmark = client.post(f"/api/v2/scientific/solvers/{SOLVER_ID}/benchmark", json={
            "source_simulation_id": fine, "benchmark_case_id": BENCHMARK_ID,
        })
        assert benchmark.status_code == 200 and benchmark.json()["passed"]
        spoof = client.post(f"/api/v2/scientific/solvers/{SOLVER_ID}/benchmark", json={
            "source_simulation_id": fine, "benchmark_case_id": BENCHMARK_ID,
            "inputs": {"speed_of_sound_m_s": 1.0}, "computed_result": 0.0,
            "expected_l2_error": 0.0,
        })
        assert spoof.status_code == 422

        refinement_payload = {
            "simulation_ids": simulations, "selected_metric": "normalized_complex_l2_error",
            "refinement_parameter": "geometry.fem_refinement.mesh.specification.target_size.value",
            "threshold": 0.05, "metric_source": "benchmark_evidence", "benchmark_id": BENCHMARK_ID,
        }
        refinement = client.post(f"/api/v2/scientific/solvers/{SOLVER_ID}/refinement", json=refinement_payload)
        assert refinement.status_code == 201, refinement.text
        refinement_record = refinement.json()
        details = refinement_record["payload"]["refinement_details"]
        assert refinement_record["payload"]["passed"] and details["refinement_monotonic"]
        assert details["observed_order"] > details["minimum_observed_order"] == 0.5
        assert details["levels"][-1]["complex_l2_error"] <= 0.05
        assert client.post(f"/api/v2/scientific/solvers/{SOLVER_ID}/refinement", json={
            **refinement_payload, "expected_refinement_result": True,
        }).status_code == 422

        trust = client.post(f"/api/v2/scientific/trust/simulations/{fine}")
        assert trust.status_code == 201, trust.text
        trust_record = trust.json()
        assert trust_record["payload"]["overall_trust"] == "MODERATE"
        assert trust_record["payload"]["dimensions"]["benchmark"]["state"] == "PASS"
        assert trust_record["payload"]["dimensions"]["refinement"]["state"] == "PASS"
        assert client.get(f"/api/v2/scientific/trust/{trust_record['id']}").status_code == 200
        trust_metadata = client.get(f"/api/v2/scientific/solvers/{SOLVER_ID}").json()
        assert trust_metadata["maximum_trust_level"] == "moderate"
        assert trust_metadata["server_validation"]["client_formula_fallback"] is False

        app.dependency_overrides[get_current_user] = lambda: {"id": other, "role": "researcher"}
        assert client.get(f"/api/simulations/{fine}/results").status_code == 404
        assert client.get(f"/api/simulations/{fine}/fields").status_code == 404
        assert client.get(f"/api/simulations/{fine}/evidence").status_code == 404
        first_field = next(iter(fields.values()))
        assert client.get(f"/api/simulations/{fine}/fields/{first_field['id']}").status_code == 404
        assert client.get(f"/api/simulations/{fine}/fields/{first_field['id']}/download").status_code == 404
        assert client.get(f"/api/v2/scientific/trust/{trust_record['id']}").status_code == 404
        with pytest.raises(LookupError):
            load_acoustic_fields(repository=repo, storage=storage, user_id=other, simulation_id=fine)

        app.dependency_overrides[get_current_user] = lambda: {"id": owner, "role": "researcher"}
        fine_model = PhysicsModelV1.model_validate(models[-1])
        fine_mesh = api._load_mesh(fine_model.mesh_id, owner)
        invalid_model = fine_model.model_copy(update={
            "numerical_settings": HarmonicAcousticSettings(frequency_hz=100000.0),
        })
        with pytest.raises(AcousticFEMError):
            execute_cad_fem(
                repository=repo, storage=storage, user_id=owner, experiment_id=experiment_id,
                design_id=None, mesh=fine_mesh, model=invalid_model, solver_id=SOLVER_ID,
                idempotency_key="final-acoustic-failure",
            )
        failed = repo.get_simulation_job_by_idempotency_key(owner, "final-acoustic-failure")
        assert failed.status == "failed" and failed.error_code == "ACOUSTIC_MESH_TOO_COARSE"
        assert repo.get_simulation_result(failed.id) is None
        assert not repo.list_field_results(failed.id)
        assert not EvidenceRepository(repository=repo).list_scientific_for_simulation(owner, failed.id)

        pressure_record = records["pressure_real_pa"]
        corrupt = tmp_path / "corrupt.npz"; corrupt.write_bytes(b"corrupt")
        storage.save_file(pressure_record.storage_object_key, corrupt)
        with pytest.raises(StorageError):
            load_acoustic_fields(repository=repo, storage=storage, user_id=owner, simulation_id=fine)

        print("ACOUSTIC_FINAL_LIFECYCLE=" + json.dumps({
            "simulation_ids": simulations, "mesh_ids": [item["mesh_id"] for item in details["levels"]],
            "field_checksums": {name: item["checksum_sha256"] for name, item in fields.items()},
            "errors": [item["complex_l2_error"] for item in details["levels"]],
            "observed_order": details["observed_order"], "trust": trust_record["payload"]["overall_trust"],
        }, sort_keys=True))
    finally:
        app.dependency_overrides.clear()
