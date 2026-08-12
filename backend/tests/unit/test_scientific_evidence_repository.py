import pytest
from types import SimpleNamespace
from app.v2.repository import EvidenceRepository
from app.v2 import repository as repository_module
from app.module2_simulation.source_resolution import (
    SimulationSource,
    SimulationSourceIntegrityError,
    SimulationSourceNotFoundError,
)

def _payload(**extra):
    return {"evidence_type":"numerical_result","status":"completed","simulation_id":"sim-a",
            "solver_id":"thermal_conduction_v1","solver_version":"1.0.0",
            "summary_metrics":{"max_temperature_c":10.0},"material_snapshot":{},
            "numerical_method":"finite_difference","convergence":{},**extra}

def test_scientific_evidence_validates_and_resolves_same_owner(tmp_path,monkeypatch):
    monkeypatch.setattr(repository_module,"resolve_simulation_source",lambda *a,**k:
        SimulationSource("sim-a","user-a","exp-a","design-a","thermal_conduction_v1","1.0.0","completed",object()))
    repo=EvidenceRepository(str(tmp_path/"evidence.db"))
    record=repo.create_scientific_evidence("user-a",_payload())
    assert record["record_type"]=="scientific_numerical_result"
    assert repo.get(record["id"],"user-a") is not None
    assert repo.get(record["id"],"user-b") is None

def test_scientific_evidence_rejects_orphan_and_contradiction(tmp_path,monkeypatch):
    repo=EvidenceRepository(str(tmp_path/"evidence.db"))
    monkeypatch.setattr(repository_module,"resolve_simulation_source",lambda *a,**k:
        (_ for _ in ()).throw(repository_module.SimulationSourceError("missing")))
    with pytest.raises(ValueError): repo.create_scientific_evidence("user-a",_payload())


def _install_simulations(monkeypatch, simulations):
    def resolve(simulation_id, user_id, **requirements):
        value = simulations.get(simulation_id)
        if value is None or value["owner"] != user_id:
            raise SimulationSourceNotFoundError("Simulation not found")
        result = SimpleNamespace(
            status=value.get("result_status", "completed"),
            summary_metrics=value.get("metrics", {"temperature_c": 50.0}),
        )
        if requirements.get("require_completed_result") and value.get("job_status", "completed") != "completed":
            raise SimulationSourceIntegrityError("Simulation job is not completed")
        metric = requirements.get("required_summary_metric")
        if metric and metric not in result.summary_metrics:
            raise SimulationSourceIntegrityError("Required metric is unavailable")
        return SimulationSource(
            simulation_id, user_id, value.get("experiment_id", "exp-a"),
            value.get("design_id", "design-a"), value.get("solver_id", "thermal_conduction_v1"),
            value.get("solver_version", "1.0.0"), value.get("job_status", "completed"), result,
        )
    monkeypatch.setattr(repository_module, "resolve_simulation_source", resolve)


def _benchmark_payload(source_simulation_id="sim-a", **extra):
    return {
        "evidence_type": "benchmark", "status": "pass", "benchmark_id": "thermal-linear",
        "metric_name": "temperature_c", "computed_value": 50.0, "reference_value": 50.0,
        "absolute_error": 0.0, "relative_error": 0.0, "tolerance": 1e-6, "passed": True,
        "source_simulation_id": source_simulation_id, "solver_id": "thermal_conduction_v1",
        "solver_version": "1.0.0", "experiment_id": "exp-a", "design_id": "design-a", **extra,
    }


def _refinement_payload(simulations, **extra):
    return {
        "evidence_type": "refinement_convergence", "status": "completed",
        "selected_metric": "temperature_c", "solver_id": "thermal_conduction_v1",
        "solver_version": "1.0.0", "experiment_id": "exp-a", "design_id": "design-a",
        "levels": [
            {"level": level, "simulation_id": simulation_id, "value": 50.0}
            for level, simulation_id in zip(("coarse", "medium", "fine"), simulations)
        ],
        **extra,
    }


def test_evidence_reference_benchmark_source_must_resolve(tmp_path, monkeypatch):
    _install_simulations(monkeypatch, {})
    repo = EvidenceRepository(str(tmp_path / "evidence.db"))
    with pytest.raises(ValueError, match="simulation source is unavailable"):
        repo.create_scientific_evidence("user-a", _benchmark_payload("orphan"))


def test_evidence_reference_benchmark_source_cannot_cross_owners(tmp_path, monkeypatch):
    _install_simulations(monkeypatch, {"sim-a": {"owner": "user-b"}})
    repo = EvidenceRepository(str(tmp_path / "evidence.db"))
    with pytest.raises(ValueError, match="simulation source is unavailable"):
        repo.create_scientific_evidence("user-a", _benchmark_payload())


def test_evidence_reference_solver_contradiction_is_rejected(tmp_path, monkeypatch):
    _install_simulations(monkeypatch, {"sim-a": {"owner": "user-a", "solver_id": "structural_linear_1d_v1"}})
    repo = EvidenceRepository(str(tmp_path / "evidence.db"))
    with pytest.raises(ValueError, match="solver_id contradicts"):
        repo.create_scientific_evidence("user-a", _benchmark_payload())


def test_evidence_reference_required_metric_must_exist(tmp_path, monkeypatch):
    _install_simulations(monkeypatch, {"sim-a": {"owner": "user-a", "metrics": {"other": 1.0}}})
    repo = EvidenceRepository(str(tmp_path / "evidence.db"))
    with pytest.raises(ValueError, match="simulation source is unavailable"):
        repo.create_scientific_evidence("user-a", _benchmark_payload())


def test_evidence_reference_refinement_simulations_cannot_be_orphaned(tmp_path, monkeypatch):
    _install_simulations(monkeypatch, {
        "coarse": {"owner": "user-a"}, "medium": {"owner": "user-a"},
    })
    repo = EvidenceRepository(str(tmp_path / "evidence.db"))
    with pytest.raises(ValueError, match="simulation source is unavailable"):
        repo.create_scientific_evidence("user-a", _refinement_payload(["coarse", "medium", "fine"]))


def test_evidence_reference_refinement_simulations_cannot_cross_owners(tmp_path, monkeypatch):
    _install_simulations(monkeypatch, {
        "coarse": {"owner": "user-a"}, "medium": {"owner": "user-b"}, "fine": {"owner": "user-a"},
    })
    repo = EvidenceRepository(str(tmp_path / "evidence.db"))
    with pytest.raises(ValueError, match="simulation source is unavailable"):
        repo.create_scientific_evidence("user-a", _refinement_payload(["coarse", "medium", "fine"]))


def test_evidence_reference_dependency_is_same_owner_scientific_evidence(tmp_path, monkeypatch):
    _install_simulations(monkeypatch, {
        "sim-a": {"owner": "user-a"}, "sim-b": {"owner": "user-b"},
    })
    repo = EvidenceRepository(str(tmp_path / "evidence.db"))
    source = repo.create_scientific_evidence("user-a", _payload())
    same_owner = repo.create_scientific_evidence(
        "user-a", _benchmark_payload(source_ids=[source["id"]])
    )
    assert same_owner["record_type"] == "scientific_benchmark"
    with pytest.raises(ValueError, match="same-owner evidence"):
        repo.create_scientific_evidence(
            "user-b", _benchmark_payload("sim-b", source_ids=[source["id"]])
        )


def test_evidence_reference_legacy_generic_cannot_masquerade_as_authoritative(tmp_path, monkeypatch):
    _install_simulations(monkeypatch, {"sim-a": {"owner": "user-a"}})
    repo = EvidenceRepository(str(tmp_path / "evidence.db"))
    legacy = repo.create("user-a", {
        "record_type": "legacy_numerical_result", "status": "completed", "experiment_id": "exp-a",
        "simulation_id": "sim-a", "parent_record_id": None, "payload": {"status": "completed"},
    })
    with pytest.raises(ValueError, match="authoritative scientific evidence"):
        repo.create_scientific_evidence(
            "user-a", _benchmark_payload(source_ids=[legacy["id"]])
        )


def test_evidence_reference_malformed_payload_fails_before_database_write(tmp_path, monkeypatch):
    _install_simulations(monkeypatch, {})
    repo = EvidenceRepository(str(tmp_path / "evidence.db"))
    malformed = _refinement_payload(["coarse", "medium", "fine"])
    del malformed["levels"][1]["simulation_id"]
    with pytest.raises(ValueError, match="Invalid authoritative scientific evidence payload"):
        repo.create_scientific_evidence("user-a", malformed)
    assert repo.list("user-a") == []
