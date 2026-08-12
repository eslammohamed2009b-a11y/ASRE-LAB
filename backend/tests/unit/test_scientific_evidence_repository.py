import pytest
from app.v2.repository import EvidenceRepository
from app.v2 import repository as repository_module
from app.module2_simulation.source_resolution import SimulationSource

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
