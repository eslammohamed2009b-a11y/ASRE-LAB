"""Study inclusion must follow the persisted scientific evidence lifecycle."""
from __future__ import annotations

import hashlib
import json

import pytest

from app.core.repository import LocalSQLiteRepository
from app.core.storage import LocalFileStorage
from app.module2_simulation import tasks
from app.module3_analysis import study_engine
from app.module3_analysis.study_engine import _screen_study_run
from app.v2.repository import EvidenceRepository


pytestmark = pytest.mark.integration


def _run(tmp_path):
    repo = LocalSQLiteRepository(tmp_path / "study-quality.sqlite3")
    simulation_id = repo.create_simulation_job("user-a", "thermal_conduction_v1")
    outcome = tasks.run_simulation_job(
        simulation_id=simulation_id, solver_id="thermal_conduction_v1", material_name="steel",
        geometry={"dimension": "1d", "length_m": 1.0, "num_elements": 10},
        boundary_conditions={"ambient_temperature_c": 100.0, "prescribed_temperature_c": 20.0},
        initial_conditions={}, numerical_settings={"max_iterations": 300, "tolerance": 1e-5},
        repository=repo, storage=LocalFileStorage(tmp_path / "objects"),
    )
    assert outcome["status"] == "completed"
    return repo, simulation_id


def _common(repo, simulation_id):
    job, result = repo.get_simulation_job(simulation_id), repo.get_simulation_result(simulation_id)
    return {
        "schema_version": "2.0", "experiment_id": job.experiment_id, "design_id": job.design_id,
        "simulation_id": simulation_id, "solver_id": result.solver_id, "solver_version": result.solver_version,
        "input_fingerprint": result.validation_metadata["input_fingerprint"], "result_hash": result.reproducibility_hash,
    }


def test_explicit_invalid_validity_evidence_excludes_an_otherwise_numerical_run(tmp_path):
    repo, simulation_id = _run(tmp_path)
    evidence = EvidenceRepository(repository=repo)
    evidence.create_scientific_evidence("user-a", {
        **_common(repo, simulation_id), "evidence_type": "validity", "status": "invalid",
        "evaluated_inputs": {}, "rules": [{"rule_id": "closure", "status": "fail"}],
    })
    screen = _screen_study_run(repo, repo.get_simulation_job(simulation_id), repo.get_simulation_result(simulation_id), "user-a")
    assert screen["status"] == "scientifically_invalid" and not screen["included"]


def test_missing_required_numerical_evidence_and_nonconvergence_are_never_eligible(tmp_path, monkeypatch):
    repo, simulation_id = _run(tmp_path)
    job, result = repo.get_simulation_job(simulation_id), repo.get_simulation_result(simulation_id)
    original = study_engine.records_by_type
    monkeypatch.setattr(study_engine, "records_by_type", lambda *args: {
        key: value for key, value in original(*args).items() if key.value != "numerical_result"
    })
    assert _screen_study_run(repo, job, result, "user-a")["status"] == "evidence_incomplete"
    monkeypatch.setattr(study_engine, "records_by_type", original)
    # The persisted result is the first numerical gate and has precedence over evidence.
    nonconverged = result.__class__(**{**result.__dict__, "converged": False})
    assert _screen_study_run(repo, job, nonconverged, "user-a")["status"] == "non_converged"


def test_validity_warnings_remain_eligible_with_an_explicit_review_status(tmp_path):
    repo, simulation_id = _run(tmp_path)
    EvidenceRepository(repository=repo).create_scientific_evidence("user-a", {
        **_common(repo, simulation_id), "evidence_type": "validity", "status": "valid_with_warnings",
        "evaluated_inputs": {}, "rules": [{"rule_id": "bounded-warning", "status": "warning"}],
        "warnings": ["bounded scope"],
    })
    screen = _screen_study_run(repo, repo.get_simulation_job(simulation_id), repo.get_simulation_result(simulation_id), "user-a")
    assert screen["status"] == "valid_with_warnings" and screen["included"]


def test_moderate_trust_is_eligible_but_tampered_trust_is_not(tmp_path):
    repo, simulation_id = _run(tmp_path)
    evidence = EvidenceRepository(repository=repo)
    records = evidence.list_scientific_for_simulation("user-a", simulation_id)
    ids = sorted(item["id"] for item in records)
    payload = {**_common(repo, simulation_id), "trust_version": "2.0", "evidence_ids": ids,
        "dimensions": {"validity": {"state": "PASS", "evidence_ids": ids}, "benchmark": {"state": "NOT_RUN", "evidence_ids": []},
                       "run_convergence": {"state": "PASS", "evidence_ids": []}, "refinement": {"state": "NOT_RUN", "evidence_ids": []}},
        "overall_trust": "MODERATE", "reason_code": "BOUNDED_WARNING_OR_REFINEMENT_NOT_RUN", "decision_rules": {}, "limitations": []}
    # Trust integrity requires each dependency exactly once across dimensions.
    payload["dimensions"] = {"all": {"state": "PASS", "evidence_ids": ids}}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    payload["trust_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
    evidence.create("user-a", {"record_type": "scientific_trust", "status": "moderate", "experiment_id": payload["experiment_id"],
        "simulation_id": simulation_id, "parent_record_id": None, "payload": payload})
    screen = _screen_study_run(repo, repo.get_simulation_job(simulation_id), repo.get_simulation_result(simulation_id), "user-a")
    assert screen["status"] == "valid_with_warnings" and screen["included"]

    payload["trust_hash"] = "tampered"
    evidence.create("user-a", {"record_type": "scientific_trust", "status": "moderate", "experiment_id": payload["experiment_id"],
        "simulation_id": simulation_id, "parent_record_id": None, "payload": payload})
    screen = _screen_study_run(repo, repo.get_simulation_job(simulation_id), repo.get_simulation_result(simulation_id), "user-a")
    assert screen["status"] == "scientifically_invalid" and not screen["included"]


def test_other_user_cannot_create_evidence_for_a_study_run(tmp_path):
    repo, simulation_id = _run(tmp_path)
    with pytest.raises(ValueError, match="simulation source is unavailable"):
        EvidenceRepository(repository=repo).create_scientific_evidence("user-b", {
            **_common(repo, simulation_id), "evidence_type": "validity", "status": "valid",
            "evaluated_inputs": {}, "rules": [],
        })
