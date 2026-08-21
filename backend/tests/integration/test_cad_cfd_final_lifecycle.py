from __future__ import annotations

import io
import json
import os
import shutil

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.repository import LocalSQLiteRepository
from app.core.storage import LocalFileStorage
from app.main import app
from app.module2_simulation import geometry_physics_router as api
from app.module2_simulation import service as simulation_service
from app.module2_simulation import tasks as cfd_tasks
from app.module2_simulation.cad_cfd_execution import (
    CFDExecutionError, create_cad_cfd_execution, load_cfd_mesh,
)
from app.module2_simulation.geometry_physics_schemas import PhysicsModelV1
from app.module2_simulation.openfoam_benchmark import (
    FV_MESH_RESOLUTIONS, benchmark_design, benchmark_domain, benchmark_physics_request,
)
from app.v2.repository import EvidenceRepository


pytestmark = pytest.mark.integration
SOLVER_ID = "cfd_openfoam_laminar_internal_3d_v1"


def test_dedicated_cfd_tasks_are_registered_only_for_cfd_queue():
    assert cfd_tasks.prepare_cfd_physics_task.name == "module2.prepare_cfd_physics_task"
    assert cfd_tasks.run_cad_cfd_job_task.name == "module2.run_cad_cfd_job_task"
    assert cfd_tasks.prepare_cfd_physics_task.queue == "cfd"
    assert cfd_tasks.run_cad_cfd_job_task.queue == "cfd"


@pytest.mark.skipif(os.getenv("ASRE_RUN_CFD_FINAL_E2E") != "1" or shutil.which("foamRun") is None,
                    reason="one explicit real final certified-FV lifecycle gate")
def test_real_authenticated_cfd_lifecycle_persistence_evidence_privacy_and_failure(tmp_path, monkeypatch):
    repo = LocalSQLiteRepository(tmp_path / "records.db")
    storage = LocalFileStorage(tmp_path / "private")
    owner, other = "cfd-owner-a", "cfd-owner-b"
    experiment_id = repo.create_experiment(owner, "Final CFD lifecycle")
    for module in (api, cfd_tasks, simulation_service):
        monkeypatch.setattr(module, "get_repository", lambda: repo)
        monkeypatch.setattr(module, "get_storage", lambda: storage)

    queues = []
    def eager_prepare(*, kwargs, queue):
        queues.append(("prepare", queue))
        return cfd_tasks.prepare_cfd_physics_task.run(**kwargs)
    def eager_execute(*, kwargs, queue):
        queues.append(("execute", queue))
        return cfd_tasks.run_cad_cfd_job_task.run(**kwargs)
    monkeypatch.setattr(cfd_tasks.prepare_cfd_physics_task, "apply_async", eager_prepare)
    monkeypatch.setattr(cfd_tasks.run_cad_cfd_job_task, "apply_async", eager_execute)

    request = {
        "experiment_id": experiment_id,
        "document": benchmark_design().model_dump(mode="json"),
        "domains": [benchmark_domain().model_dump(mode="json")],
        "resolution": FV_MESH_RESOLUTIONS["coarse"].model_dump(mode="json"),
        "physics": benchmark_physics_request().model_dump(mode="json"),
    }
    app.dependency_overrides[get_current_user] = lambda: {"id": owner, "role": "researcher"}
    try:
        client = TestClient(app)
        prepared = client.post("/api/geometry-physics/physics/cfd", json=request,
                               headers={"Idempotency-Key": "final-cfd-preparation"})
        assert prepared.status_code == 202, prepared.text
        preparation = prepared.json()
        assert preparation["status"] == "completed", preparation
        model, mesh = preparation["physics_model"], preparation["mesh"]
        assert queues == [("prepare", "cfd")]
        assert model["mesh_id"] == mesh["mesh_id"] and model["mesh_hash"] == mesh["mesh_hash"]
        assert model["physics_hash"]
        for mapping in model["semantic_mappings"]:
            assert mapping["mapping_type"] == "cfd_openfoam_patch"
            assert mapping["face_ids"] == list(range(mapping["start_face"], mapping["start_face"] + mapping["face_count"]))
        serialized = json.dumps(preparation).lower()
        assert not any(token in serialized for token in ("object_key", "bucket", "temporary", "host path", "container path"))

        reloaded = load_cfd_mesh(repository=repo, storage=storage, owner_id=owner, mesh_id=mesh["mesh_id"])
        assert (reloaded.mesh_id, reloaded.mesh_hash) == (mesh["mesh_id"], mesh["mesh_hash"])
        assert [item.model_dump(mode="json") for item in reloaded.semantic_patches] == mesh["semantic_patches"]
        assert client.get(f"/api/geometry-physics/meshes/cfd/{mesh['mesh_id']}").status_code == 200
        assert client.get(f"/api/geometry-physics/physics/{model['physics_model_id']}").status_code == 200

        executed = client.post(
            f"/api/geometry-physics/physics/cfd/{model['physics_model_id']}/execute",
            json={"solver_id": SOLVER_ID}, headers={"Idempotency-Key": "final-cfd-execution"},
        )
        assert executed.status_code == 202, executed.text
        simulation_id = executed.json()["simulation_id"]
        assert executed.json()["status"] == "completed", {
            "error_code": repo.get_simulation_job(simulation_id).error_code,
            "safe_error_message": repo.get_simulation_job(simulation_id).safe_error_message,
        }
        assert queues == [("prepare", "cfd"), ("execute", "cfd")]

        retry = client.post(
            f"/api/geometry-physics/physics/cfd/{model['physics_model_id']}/execute",
            json={"solver_id": SOLVER_ID}, headers={"Idempotency-Key": "final-cfd-execution"},
        )
        assert retry.status_code == 202 and retry.json()["simulation_id"] == simulation_id
        assert queues == [("prepare", "cfd"), ("execute", "cfd")]

        result_response = client.get(f"/api/simulations/{simulation_id}/results")
        assert result_response.status_code == 200, result_response.text
        result = result_response.json()["result"]
        assert result["solver_id"] == SOLVER_ID and result["status"] == "completed"
        assert result["reproducibility_hash"] and result["validation_metadata"]["physics_model_hash"] == model["physics_hash"]
        assert result["validation_metadata"]["mesh_hash"] == mesh["mesh_hash"]
        conditions = result["validation_metadata"]["convergence_conditions"]
        assert conditions["simple_converged"] and conditions["finite_reviewed_fields"]
        assert conditions["final_u_residual"] <= conditions["u_tolerance"]
        assert conditions["final_p_residual"] <= conditions["p_tolerance"]
        assert conditions["normalized_mass_imbalance"] <= conditions["mass_imbalance_limit"]

        fields_response = client.get(f"/api/simulations/{simulation_id}/fields")
        assert fields_response.status_code == 200
        fields = {item["variable_name"]: item for item in fields_response.json()}
        assert set(fields) == {"U", "p"}
        assert fields["U"]["unit"] == "m/s" and fields["U"]["array_shape"] == [mesh["cell_count"], 3]
        assert fields["U"]["grid_metadata"]["location_type"] == "cell_centered" and fields["U"]["checksum_sha256"]
        assert fields["p"]["unit"] == "m2/s2" and fields["p"]["array_shape"] == [mesh["cell_count"]]
        assert fields["p"]["grid_metadata"]["quantity"] == "kinematic_pressure"
        assert fields["p"]["grid_metadata"]["physical_pressure_conversion"] == "rho * p"
        assert fields["p"]["grid_metadata"]["density_kg_m3"] > 0 and fields["p"]["checksum_sha256"]
        for field in fields.values():
            assert client.get(f"/api/simulations/{simulation_id}/fields/{field['id']}/download").status_code == 200

        evidence_response = client.get(f"/api/simulations/{simulation_id}/evidence")
        assert evidence_response.status_code == 200
        evidence = evidence_response.json()
        types = [item["record_type"] for item in evidence]
        assert sum("numerical_result" in item for item in types) == 1
        assert sum("field_result" in item for item in types) == 2
        assert sum("validity" in item for item in types) == 1
        assert sum("run_convergence" in item for item in types) == 1
        assert not any("benchmark" in item or "refinement" in item for item in types)
        convergence = next(item for item in evidence if "run_convergence" in item["record_type"])
        assert "SIMPLE converged" in convergence["payload"]["criterion"] and convergence["payload"]["passed"] is True

        trust = client.get(f"/api/v2/scientific/solvers/{SOLVER_ID}")
        assert trust.status_code == 200
        assert trust.json()["maximum_trust_level"] == "moderate"
        assert trust.json()["server_validation"]["client_formula_fallback"] is False
        blocked_benchmark = client.post(f"/api/v2/scientific/solvers/{SOLVER_ID}/benchmark", json={})
        assert blocked_benchmark.status_code == 422

        print("ASRE_REAL_CFD_FINAL_LIFECYCLE=" + json.dumps({
            "mesh_id": mesh["mesh_id"],
            "mesh_hash": mesh["mesh_hash"],
            "physics_hash": model["physics_hash"],
            "cells": mesh["cell_count"],
            "solver": result["solver_id"],
            "iterations": result["convergence"]["iterations"],
            "u_residual": conditions["final_u_residual"],
            "p_residual": conditions["final_p_residual"],
            "mass_imbalance": conditions["normalized_mass_imbalance"],
            "u_shape": fields["U"]["array_shape"],
            "u_checksum": fields["U"]["checksum_sha256"],
            "p_shape": fields["p"]["array_shape"],
            "p_checksum": fields["p"]["checksum_sha256"],
        }, sort_keys=True))

        app.dependency_overrides[get_current_user] = lambda: {"id": other, "role": "researcher"}
        assert client.get(f"/api/geometry-physics/physics/cfd/preparations/{preparation['preparation_id']}").status_code == 404
        assert client.get(f"/api/geometry-physics/meshes/cfd/{mesh['mesh_id']}").status_code == 404
        assert client.get(f"/api/geometry-physics/physics/{model['physics_model_id']}").status_code == 404
        assert client.get(f"/api/simulations/{simulation_id}/results").status_code == 404
        assert client.get(f"/api/simulations/{simulation_id}/fields").status_code == 404
        assert client.get(f"/api/simulations/{simulation_id}/evidence").status_code == 404

        app.dependency_overrides[get_current_user] = lambda: {"id": owner, "role": "researcher"}
        mismatched = reloaded.model_copy(update={"mesh_id": "mismatched-fv", "mesh_hash": "f" * 64})
        with pytest.raises(CFDExecutionError) as failed:
            create_cad_cfd_execution(
                repository=repo, storage=storage, user_id=owner, experiment_id=experiment_id,
                model=PhysicsModelV1.model_validate(model), mesh=mismatched,
                idempotency_key="final-cfd-failure",
            )
        failed_job = repo.get_simulation_job(failed.value.simulation_id)
        assert failed_job.status == "failed" and failed_job.error_code == "AUTHORITATIVE_MESH_REQUIRED"
        assert repo.get_simulation_result(failed_job.id) is None
        assert not repo.list_field_results(failed_job.id)
        assert not EvidenceRepository(repository=repo).list_scientific_for_simulation(owner, failed_job.id)

        u_record = next(item for item in repo.list_field_results(simulation_id) if item.variable_name == "U")
        with np.load(io.BytesIO(storage.open_bytes(u_record.storage_object_key)), allow_pickle=False) as archive:
            assert np.isfinite(archive["field"]).all()
    finally:
        app.dependency_overrides.clear()
