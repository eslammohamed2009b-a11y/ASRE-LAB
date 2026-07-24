from __future__ import annotations

import pytest

from app.core.repository import LocalSQLiteRepository
from app.core.storage import LocalFileStorage
from app.module2_simulation.coupling import CouplingNotFoundError, ThermalStructuralCouplingRequest, get_coupling, run_thermal_structural_coupling

pytestmark = pytest.mark.integration


def _setup(tmp_path):
    repo = LocalSQLiteRepository(tmp_path/"coupling.db")
    storage = LocalFileStorage(tmp_path/"objects")
    experiment = repo.create_experiment("user-a","coupling")
    design = repo.create_design_model(experiment,"user-a","bar",{"length_m":1.0},{"length_m":"m"},0)
    return repo, storage, experiment, design


def test_restrained_thermal_expansion_benchmark_provenance_and_durability(tmp_path):
    repo, storage, experiment, design = _setup(tmp_path)
    request = ThermalStructuralCouplingRequest(experiment_id=experiment,design_id=design,material="steel",
        length_m=1.0,cross_section_area_m2=0.01,num_elements=10,reference_temperature_c=20,hot_end_temperature_c=120)
    record = run_thermal_structural_coupling(request,"user-a",repo,storage)
    assert record.status == "completed"
    assert len(record.source_simulation_ids) == 2
    assert record.result["delta_temperature_k"] == pytest.approx(50.0)
    assert record.result["structural_metrics"]["max_stress_pa"] == pytest.approx(120e6)
    assert len(repo.list_field_results(record.source_simulation_ids[0])) == 1
    assert len(repo.list_field_results(record.source_simulation_ids[1])) == 2
    restarted = LocalSQLiteRepository(tmp_path/"coupling.db")
    assert get_coupling(record.id,"user-a",restarted).source_simulation_ids == record.source_simulation_ids
    with pytest.raises(CouplingNotFoundError):
        get_coupling(record.id,"user-b",restarted)


def test_coupling_rejects_incompatible_design_and_preserves_partial_failure(tmp_path):
    repo, storage, experiment, design = _setup(tmp_path)
    request = ThermalStructuralCouplingRequest(experiment_id=experiment,design_id="missing",material="steel",
        length_m=1,cross_section_area_m2=.01,hot_end_temperature_c=100)
    with pytest.raises(CouplingNotFoundError):
        run_thermal_structural_coupling(request,"user-a",repo,storage)
    request.design_id, request.material = design, "concrete"
    record = run_thermal_structural_coupling(request,"user-a",repo,storage)
    assert record.status == "partial_failure"
    assert len(record.source_simulation_ids) == 2
    assert repo.get_simulation_job(record.source_simulation_ids[0]).status == "completed"
    assert repo.get_simulation_job(record.source_simulation_ids[1]).status == "failed"
