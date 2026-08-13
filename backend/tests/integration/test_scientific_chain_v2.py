from __future__ import annotations

import pytest

from app.core.repository import LocalSQLiteRepository
from app.core.storage import LocalFileStorage
from app.module2_simulation.tasks import run_simulation_job
from app.module2_simulation.source_resolution import resolve_simulation_source
from app.module3_analysis.schemas import AnalysisCreateRequest, ObjectiveSpec
from app.module3_analysis.service import run_experiment_analysis
from app.v2.claim_integrity import classify_claim
from app.v2.evidence_integrity import records_by_type
from app.v2.evidence_models import EvidenceType
from app.v2.refinement import create_refinement_evidence
from app.v2.repository import EvidenceRepository
from app.v2.scientific_trust import REGISTRY, benchmark
from app.v2.trust_v2 import derive_trust_record

pytestmark = pytest.mark.integration


def test_real_solver_to_evidence_trust_analysis_and_claim_chain(tmp_path):
    repo=LocalSQLiteRepository(tmp_path/"chain.db")
    storage=LocalFileStorage(tmp_path/"objects")
    owner="authenticated-owner"
    experiment=repo.create_experiment(owner,"real scientific chain")
    design=repo.create_design_model(
        experiment,owner,"channel",{"length_m":.1,"height_m":.01},{"length_m":"m","height_m":"m"},0,"completed",
    )
    simulations=[]
    for ny in (11,21,41):
        simulation=repo.create_simulation_job(owner,"cfd_laminar_channel_2d_v1",experiment,design)
        outcome=run_simulation_job(
            simulation_id=simulation,solver_id="cfd_laminar_channel_2d_v1",material_name="air",
            geometry={"dimension":"2d","length_m":.1,"height_m":.01,"grid_resolution":9,"grid_resolution_y":ny},
            boundary_conditions={"pressure_gradient_pa_m":-.01},initial_conditions={},numerical_settings={},
            experiment_id=experiment,design_id=design,repository=repo,storage=storage,
        )
        assert outcome["status"] == "completed"
        simulations.append(simulation)

    scientific=EvidenceRepository(repository=repo)
    fine_source=resolve_simulation_source(
        simulations[-1],owner,require_completed_result=True,repository=repo,
    )
    numerical=records_by_type(scientific,owner,fine_source)[EvidenceType.NUMERICAL_RESULT][-1][0]
    item=REGISTRY.get("cfd_laminar_channel_2d_v1")
    computed=fine_source.result.summary_metrics[item.benchmark_metric]
    evaluated=benchmark(item,{
        "pressure_gradient_pa_m":-.01,"height_m":.01,"viscosity_pa_s":1.81e-5,
    },computed,simulations[-1])
    benchmark_record=scientific.create_scientific_evidence(owner,{
        "evidence_type":"benchmark","schema_version":"2.0","experiment_id":experiment,
        "design_id":design,"simulation_id":simulations[-1],"solver_id":fine_source.solver_id,
        "solver_version":fine_source.solver_version,
        "input_fingerprint":fine_source.result.validation_metadata["input_fingerprint"],
        "result_hash":fine_source.result.reproducibility_hash,"source_ids":[numerical["id"]],
        "status":"pass" if evaluated["passed"] else "fail","benchmark_id":evaluated["benchmark_id"],
        "metric_name":evaluated["selected_metric"],"computed_value":evaluated["computed_result"],
        "reference_value":evaluated["reference_result"],"absolute_error":evaluated["absolute_error"],
        "relative_error":evaluated["relative_error"],"tolerance":evaluated["declared_tolerance"],
        "passed":evaluated["passed"],"source_simulation_id":simulations[-1],
    })
    refinement=create_refinement_evidence(
        owner,simulations,"mean_velocity_m_s","geometry.grid_resolution_y",.02,repository=repo,
    )
    trust=derive_trust_record(owner,simulations[-1],repository=repo)
    analysis=run_experiment_analysis(
        experiment,owner,AnalysisCreateRequest(objectives=[
            ObjectiveSpec(column="metric.mean_velocity_m_s",direction="maximize",weight=1),
        ]),repo,
    )
    best=analysis.result["ranking"]["ranking"][0]
    claim=classify_claim(
        scientific,owner,"The declared objective has a deterministic ranked candidate.",
        [analysis.analysis_evidence_id],semantic_context={
            "experiment_id":experiment,"candidates":[{
                "design_id":best["design_id"],"evidence_ids":[analysis.analysis_evidence_id],
                "metric_assertions":[
                    {"metric_name":name.removeprefix("metric."),"value":value}
                    for name,value in best["objective_values"].items()
                ],
            }],
        },
    )

    assert fine_source.result.validation_metadata["input_fingerprint"]
    assert fine_source.result.reproducibility_hash
    assert benchmark_record["payload"]["passed"] is True
    assert refinement["payload"]["passed"] is True
    assert trust["payload"]["overall_trust"] in {"MODERATE","HIGH"}
    assert analysis.analysis_evidence_id and claim["classification"] == "finding"
    chain_ids=[numerical["id"],benchmark_record["id"],refinement["id"],trust["id"],analysis.analysis_evidence_id]
    assert all(scientific.get(record_id,owner) is not None for record_id in chain_ids)
    assert all(scientific.get(record_id,"other-owner") is None for record_id in chain_ids)


def test_owner_scoped_trust_api_derives_v2_and_blocks_generic_fabrication(
    authorized_client, monkeypatch, tmp_path,
):
    db_path=tmp_path/"trust-api.db"; monkeypatch.setenv("LOCAL_PERSISTENCE_DB_PATH",str(db_path))
    repo=LocalSQLiteRepository(db_path); storage=LocalFileStorage(tmp_path/"api-objects"); owner="user-test"
    experiment=repo.create_experiment(owner,"trust api")
    design=repo.create_design_model(experiment,owner,"channel",{}, {},0,"completed")
    simulation=repo.create_simulation_job(owner,"cfd_laminar_channel_2d_v1",experiment,design)
    run_simulation_job(
        simulation_id=simulation,solver_id="cfd_laminar_channel_2d_v1",material_name="air",
        geometry={"dimension":"2d","length_m":.1,"height_m":.01,"grid_resolution":9,"grid_resolution_y":21},
        boundary_conditions={"pressure_gradient_pa_m":-.01},initial_conditions={},numerical_settings={},
        experiment_id=experiment,design_id=design,repository=repo,storage=storage,
    )
    computed=repo.get_simulation_result(simulation).summary_metrics["maximum_velocity_m_s"]
    benchmark_response=authorized_client.post(
        "/api/v2/scientific/solvers/cfd_laminar_channel_2d_v1/benchmark",json={
            "inputs":{"pressure_gradient_pa_m":-.01,"height_m":.01,"viscosity_pa_s":1.81e-5},
            "computed_result":computed,"source_simulation_id":simulation,
        },
    )
    assert benchmark_response.status_code == 200 and benchmark_response.json()["evidence_id"]
    created=authorized_client.post(f"/api/v2/scientific/trust/simulations/{simulation}")
    assert created.status_code == 201
    record=created.json(); assert record["payload"]["trust_version"] == "2.0"
    assert authorized_client.get(f"/api/v2/scientific/trust/{record['id']}").status_code == 200
    blocked=authorized_client.post("/api/v2/evidence",json={
        "record_type":"scientific_trust","status":"high","payload":{"evidence_ids":[]},
    })
    assert blocked.status_code == 422
    from app.core.auth import get_current_user
    from app.main import app
    app.dependency_overrides[get_current_user]=lambda:{"id":"other-owner","role":"researcher"}
    try: assert authorized_client.get(f"/api/v2/scientific/trust/{record['id']}").status_code == 404
    finally: app.dependency_overrides[get_current_user]=lambda:{"id":owner,"role":"researcher"}
