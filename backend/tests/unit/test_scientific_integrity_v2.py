from __future__ import annotations

import hashlib

import pytest

from app.core.repository import LocalSQLiteRepository, SimulationResultRecord
from app.module3_analysis.dataset import scientific_dataset_hash
from app.module3_analysis.schemas import DatasetRow
from app.v2.claim_integrity import classify_claim
from app.v2.refinement import create_refinement_evidence
from app.v2.repository import EvidenceRepository
from app.v2.scientific_trust import REGISTRY, benchmark, reference_only
from app.v2.trust_v2 import derive_trust_record


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _case(tmp_path, *, owner="owner-a", solver="thermal_conduction_v1", version="1.0.0"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = LocalSQLiteRepository(tmp_path / f"{owner}.db")
    experiment = repo.create_experiment(owner, "scientific trust")
    design = repo.create_design_model(experiment, owner, "slab", {"length_m": 1.0}, {"length_m": "m"}, 0)
    return repo, owner, experiment, design, solver, version


def _simulation(case, *, grid=10, metric=50.0, validity="valid", convergence="completed"):
    repo, owner, experiment, design, solver, version = case
    simulation = repo.create_simulation_job(owner, solver, experiment, design)
    repo.record_simulation_input(
        simulation, "steel", {"density": 7800.0}, {"density": "kg/m^3"}, {},
        {"cold_c": 0.0, "hot_c": 100.0}, {}, {"num_elements": grid},
    )
    fingerprint, result_hash = _digest(f"input:{simulation}"), _digest(f"result:{simulation}")
    repo.record_simulation_result(SimulationResultRecord(
        simulation_id=simulation, solver_id=solver, solver_version=version,
        converged=convergence == "completed", residual=0.0, iteration_count=1, tolerance=1e-6,
        summary_metrics={"temperature_c": metric}, numerical_method="bounded_test_method",
        validation_metadata={"input_fingerprint": fingerprint, "material_properties_used": {"density": 7800.0}},
        reproducibility_hash=result_hash,
    ))
    repo.update_simulation_job(simulation, status="completed", progress_percent=100)
    evidence = EvidenceRepository(repository=repo)
    common = {
        "schema_version":"2.0","experiment_id":experiment,"design_id":design,
        "simulation_id":simulation,"solver_id":solver,"solver_version":version,
        "input_fingerprint":fingerprint,"result_hash":result_hash,
    }
    numerical = evidence.create_scientific_evidence(owner, {
        **common,"evidence_type":"numerical_result","status":"completed",
        "summary_metrics":{"temperature_c":metric},"material_snapshot":{"density":7800.0},
        "numerical_method":"bounded_test_method","convergence":{"converged":convergence == "completed"},
    })
    validity_record = evidence.create_scientific_evidence(owner, {
        **common,"evidence_type":"validity","status":validity,
        "evaluated_inputs":{"num_elements":grid},"rules":[],
    })
    convergence_record = evidence.create_scientific_evidence(owner, {
        **common,"evidence_type":"run_convergence","status":convergence,
        "source_ids":[numerical["id"]],"metric_type":"algebraic_residual",
        "metric_value":0.0,"tolerance":1e-6,"iterations":1,"criterion":"residual <= tolerance",
        "passed":True if convergence == "completed" else None,
    })
    return simulation, numerical, validity_record, convergence_record


def _benchmark(case, simulation, numerical, *, passed=True):
    repo, owner, experiment, design, solver, version = case
    result = repo.get_simulation_result(simulation)
    capability=REGISTRY.get(solver)
    actual=float(result.summary_metrics[capability.benchmark_metric])
    reference=actual if passed else actual*.8
    absolute=abs(actual-reference); relative=absolute/max(abs(reference),1e-15)
    return EvidenceRepository(repository=repo).create_scientific_evidence(owner, {
        "evidence_type":"benchmark","schema_version":"2.0","experiment_id":experiment,
        "design_id":design,"simulation_id":simulation,"solver_id":solver,"solver_version":version,
        "input_fingerprint":result.validation_metadata["input_fingerprint"],
        "result_hash":result.reproducibility_hash,"source_ids":[numerical["id"]],
        "status":"pass" if passed else "fail","benchmark_id":capability.benchmark_id,
        "metric_name":capability.benchmark_metric,"computed_value":actual,
        "reference_value":reference,"absolute_error":absolute,
        "relative_error":relative,"tolerance":capability.benchmark_tolerance,"passed":passed,
        "source_simulation_id":simulation,
    })


def test_missing_benchmark_prevents_high_and_is_not_run(tmp_path):
    case = _case(tmp_path); simulation, *_ = _simulation(case)
    trust = derive_trust_record(case[1], simulation, repository=case[0])["payload"]
    assert trust["overall_trust"] == "LOW"
    assert trust["dimensions"]["benchmark"] == {"state":"NOT_RUN","evidence_ids":[]}


def test_failed_validity_is_invalid(tmp_path):
    case = _case(tmp_path); simulation, numerical, *_ = _simulation(case, validity="invalid")
    _benchmark(case, simulation, numerical)
    assert derive_trust_record(case[1], simulation, repository=case[0])["payload"]["overall_trust"] == "INVALID"


def test_benchmark_pass_requires_persisted_authoritative_evidence(tmp_path):
    case = _case(tmp_path); simulation, numerical, *_ = _simulation(case)
    assert reference_only(REGISTRY.get(case[4]), {"cold_c":0,"hot_c":100})["passed"] is None
    assert derive_trust_record(case[1], simulation, repository=case[0])["payload"]["dimensions"]["benchmark"]["state"] == "NOT_RUN"
    record = _benchmark(case, simulation, numerical)
    trust = derive_trust_record(case[1], simulation, repository=case[0])["payload"]
    assert trust["dimensions"]["benchmark"] == {"state":"PASS","evidence_ids":[record["id"]]}


def test_failed_authoritative_benchmark_produces_low_trust(tmp_path):
    case=_case(tmp_path); simulation,numerical,*_=_simulation(case); _benchmark(case,simulation,numerical,passed=False)
    trust=derive_trust_record(case[1],simulation,repository=case[0])["payload"]
    assert trust["dimensions"]["benchmark"]["state"] == "FAIL"
    assert trust["overall_trust"] == "LOW"


def test_run_convergence_states_come_from_evidence(tmp_path):
    case = _case(tmp_path); simulation, numerical, _, convergence = _simulation(case)
    _benchmark(case, simulation, numerical)
    trust = derive_trust_record(case[1], simulation, repository=case[0])["payload"]
    assert trust["dimensions"]["run_convergence"] == {"state":"PASS","evidence_ids":[convergence["id"]]}


def test_direct_method_can_be_not_applicable(tmp_path):
    case = _case(tmp_path); simulation, numerical, _, _ = _simulation(case, convergence="not_applicable")
    _benchmark(case, simulation, numerical)
    trust = derive_trust_record(case[1], simulation, repository=case[0])["payload"]
    assert trust["dimensions"]["run_convergence"]["state"] == "NOT_APPLICABLE"


def test_no_refinement_is_not_run_and_not_high(tmp_path):
    case = _case(tmp_path); simulation, numerical, *_ = _simulation(case); _benchmark(case, simulation, numerical)
    trust = derive_trust_record(case[1], simulation, repository=case[0])["payload"]
    assert trust["dimensions"]["refinement"]["state"] == "NOT_RUN"
    assert trust["overall_trust"] == "MODERATE"


def test_real_refinement_pass_and_failure_affect_trust(tmp_path):
    case = _case(tmp_path)
    sources = [_simulation(case, grid=grid, metric=value) for grid, value in zip((10,20,40),(49.0,49.8,50.0))]
    _benchmark(case, sources[-1][0], sources[-1][1])
    refinement = create_refinement_evidence(
        case[1],[item[0] for item in sources],"temperature_c","geometry.num_elements",.02,repository=case[0],
    )
    trust = derive_trust_record(case[1], sources[-1][0], repository=case[0])["payload"]
    assert refinement["payload"]["passed"] is True and trust["overall_trust"] == "HIGH"

    failed_case = _case(tmp_path / "failed")
    failed = [_simulation(failed_case, grid=grid, metric=value) for grid, value in zip((10,20,40),(40.0,45.0,50.0))]
    _benchmark(failed_case, failed[-1][0], failed[-1][1])
    create_refinement_evidence(failed_case[1],[item[0] for item in failed],"temperature_c","geometry.num_elements",.02,repository=failed_case[0])
    assert derive_trust_record(failed_case[1],failed[-1][0],repository=failed_case[0])["payload"]["overall_trust"] == "LOW"


def test_refinement_rejects_bad_sources_and_incompatible_science(tmp_path):
    case = _case(tmp_path); items = [_simulation(case, grid=x) for x in (10,20,40)]
    with pytest.raises(Exception):
        create_refinement_evidence(case[1],[items[0][0],items[1][0],"missing"],"temperature_c","geometry.num_elements",repository=case[0])
    with pytest.raises(ValueError, match="progress"):
        create_refinement_evidence(case[1],[x[0] for x in reversed(items)],"temperature_c","geometry.num_elements",repository=case[0])
    other = _case(tmp_path / "other", owner="owner-b"); foreign = _simulation(other)[0]
    with pytest.raises(Exception):
        create_refinement_evidence(case[1],[items[0][0],items[1][0],foreign],"temperature_c","geometry.num_elements",repository=case[0])


def test_refinement_rejects_wrong_solver_version_setup_and_metric(tmp_path):
    case = _case(tmp_path); items = [_simulation(case, grid=x) for x in (10,20,40)]
    wrong_solver_case=(case[0],case[1],case[2],case[3],"modal_eigen_1d_v1",case[5])
    wrong_sim = _simulation(wrong_solver_case,grid=40)[0]
    with pytest.raises(ValueError, match="same solver"):
        create_refinement_evidence(case[1],[items[0][0],items[1][0],wrong_sim],"temperature_c","geometry.num_elements",repository=case[0])
    wrong_version_case=(case[0],case[1],case[2],case[3],case[4],"9.9.9")
    wrong_version_sim=_simulation(wrong_version_case,grid=40)[0]
    with pytest.raises(ValueError, match="same solver version"):
        create_refinement_evidence(case[1],[items[0][0],items[1][0],wrong_version_sim],"temperature_c","geometry.num_elements",repository=case[0])
    with pytest.raises(Exception):
        create_refinement_evidence(case[1],[x[0] for x in items],"missing_metric","geometry.num_elements",repository=case[0])
    # A different boundary condition is a different physical case.
    conn = case[0]._connect()
    try:
        conn.execute("update simulation_inputs set boundary_conditions=? where simulation_id=?", ('{"cold_c":10,"hot_c":100}',items[1][0])); conn.commit()
    finally: conn.close()
    with pytest.raises(ValueError, match="physical setup"):
        create_refinement_evidence(case[1],[x[0] for x in items],"temperature_c","geometry.num_elements",repository=case[0])


def test_refinement_is_deterministic_and_anonymous_values_are_not_an_api(tmp_path):
    case = _case(tmp_path); items = [_simulation(case, grid=g, metric=v) for g,v in zip((10,20,40),(49,49.8,50))]
    first = create_refinement_evidence(case[1],[x[0] for x in items],"temperature_c","geometry.num_elements",repository=case[0])
    second = create_refinement_evidence(case[1],[x[0] for x in items],"temperature_c","geometry.num_elements",repository=case[0])
    assert first["id"] == second["id"]
    with pytest.raises(Exception):
        create_refinement_evidence(case[1],[1.0,2.0,3.0],"temperature_c","geometry.num_elements",repository=case[0])


def _row(simulation="sim-a", value=1.0):
    return DatasetRow(design_id="design",simulation_id=simulation,solver_id="solver",solver_version="1",
                      values={"metric.x":value},converged=True,simulation_status="completed",evidence_ids=["evidence"])


def test_dataset_hash_is_order_independent_but_scientifically_sensitive():
    rows=[_row("sim-b",2),_row("sim-a",1)]; columns=["metric.x"]; units={"metric.x":"m"}
    first=scientific_dataset_hash("exp",rows,columns,units)
    assert first == scientific_dataset_hash("exp",list(reversed(rows)),columns,units)
    assert first != scientific_dataset_hash("exp",[_row("sim-b",3),_row("sim-a",1)],columns,units)
    assert first != scientific_dataset_hash("exp",[_row("sim-c",2),_row("sim-a",1)],columns,units)


@pytest.mark.parametrize("operational", [
    {"created_at":"tomorrow"},{"signed_url":"https://signed.invalid"},{"request_id":"different"},
])
def test_dataset_hash_excludes_operational_metadata(operational):
    base=_row(); noisy=DatasetRow.model_validate({**base.model_dump(),**operational})
    assert scientific_dataset_hash("exp",[base],["metric.x"],{"metric.x":"m"}) == scientific_dataset_hash("exp",[noisy],["metric.x"],{"metric.x":"m"})


def test_claim_integrity_rejects_missing_cross_owner_and_legacy(tmp_path):
    case=_case(tmp_path); simulation,numerical,*_=_simulation(case)
    repo=EvidenceRepository(repository=case[0])
    assert classify_claim(repo,case[1],"Temperature is 50 C",[numerical["id"]])["classification"] == "finding"
    assert classify_claim(repo,case[1],"Temperature is 50 C",["missing"])["classification"] == "insufficient_evidence"
    assert classify_claim(repo,"owner-b","Temperature is 50 C",[numerical["id"]])["classification"] == "insufficient_evidence"
    legacy=repo.create(case[1],{"record_type":"legacy_result","status":"completed","experiment_id":case[2],"simulation_id":simulation,"parent_record_id":None,"payload":{}})
    assert classify_claim(repo,case[1],"Temperature is 50 C",[legacy["id"]])["classification"] == "insufficient_evidence"
    assert classify_claim(repo,case[1],"Perhaps temperature changes",[],requested_classification="hypothesis")["classification"] == "hypothesis"


def test_trust_is_deterministic_owner_scoped_and_references_resolve(tmp_path):
    case=_case(tmp_path); simulation,numerical,*_=_simulation(case); _benchmark(case,simulation,numerical)
    first=derive_trust_record(case[1],simulation,repository=case[0]); second=derive_trust_record(case[1],simulation,repository=case[0])
    assert first["id"] == second["id"] and first["payload"]["trust_hash"] == second["payload"]["trust_hash"]
    repo=EvidenceRepository(repository=case[0])
    assert all(repo.get(item,case[1]) for item in first["payload"]["evidence_ids"])
    with pytest.raises(Exception): derive_trust_record("owner-b",simulation,repository=case[0])
    forged=repo.create(case[1],{"record_type":"scientific_trust","status":"high","experiment_id":case[2],"simulation_id":simulation,"parent_record_id":None,"payload":{"trust_version":"2.0","evidence_ids":["fabricated"]}})
    assert classify_claim(repo,case[1],"High trust",[forged["id"]])["classification"] == "insufficient_evidence"
