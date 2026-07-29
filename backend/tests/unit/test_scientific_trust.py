import math
import pytest
from fastapi.testclient import TestClient
from app.core.auth import get_current_user
from app.main import app
from app.v2.repository import EvidenceRepository
from app.v2.scientific_trust import (
    REGISTRY, TrustCapability, TrustRegistry, benchmark, confidence, convergence, validate,
)

THERMAL=REGISTRY.get("thermal_conduction_v1")

def test_all_real_capabilities_and_coupling_are_registered():
    assert {x.solver_id for x in REGISTRY.list()} == {
        "thermal_conduction_v1","structural_linear_1d_v1","modal_eigen_1d_v1",
        "acoustic_duct_1d_v1","electrostatic_rectangular_2d_v1",
        "cfd_laminar_channel_2d_v1","thermal_structural_one_way_v1"}

def test_duplicate_registration_is_rejected():
    registry=TrustRegistry()
    with pytest.raises(ValueError): registry.register(THERMAL)

def test_valid_warning_and_invalid_envelopes():
    assert validate(THERMAL,{"length_m":1,"num_elements":20})["status"]=="valid"
    warning=validate(THERMAL,{"length_m":1,"num_elements":2})
    assert warning["status"]=="valid_with_warnings"
    assert warning["rules"][0]["code"]=="NEAR_VALIDITY_BOUNDARY"
    invalid=validate(THERMAL,{"length_m":0,"num_elements":20})
    assert invalid["status"]=="invalid"
    assert invalid["rules"][0]["code"]=="OUTSIDE_VALIDITY_ENVELOPE"

@pytest.mark.parametrize(("solver_id","inputs","expected"),[
 ("thermal_conduction_v1",{"cold_c":0,"hot_c":100,"position_fraction":.25},25),
 ("structural_linear_1d_v1",{"load_n":100,"length_m":2,"youngs_modulus_pa":1000,"area_m2":.5},.4),
 ("modal_eigen_1d_v1",{"stiffness_n_m":400,"mass_kg":4},10/(2*math.pi)),
 ("acoustic_duct_1d_v1",{"speed_m_s":340,"length_m":2},85),
 ("electrostatic_rectangular_2d_v1",{"left_v":0,"right_v":10,"width_m":2},5),
 ("cfd_laminar_channel_2d_v1",{"pressure_gradient_pa_m":-8,"height_m":1,"viscosity_pa_s":1},1),
 ("thermal_structural_one_way_v1",{"youngs_modulus_pa":200,"alpha_1_k":.01,"delta_temperature_k":5},10),
])
def test_benchmark_formula_correctness(solver_id,inputs,expected):
    result=benchmark(REGISTRY.get(solver_id),inputs)
    assert result["reference_result"]==pytest.approx(expected)
    assert result["passed"]

def test_benchmark_pass_and_failure():
    inputs={"cold_c":0,"hot_c":100}
    assert benchmark(THERMAL,inputs,50)["passed"]
    assert not benchmark(THERMAL,inputs,60)["passed"]

def test_convergence_and_nonconvergence():
    good=convergence(THERMAL,[10,10.1,10.11],threshold=.02)
    assert good["converged"] and good["recommended_level"]=="medium"
    bad=convergence(THERMAL,[10,11,13],threshold=.02)
    assert not bad["converged"] and bad["warnings"][0]["code"]=="POOR_CONVERGENCE"

def test_non_applicable_convergence_is_explicit():
    result=convergence(REGISTRY.get("thermal_structural_one_way_v1"),[])
    assert result=={"applicable":False,"status":"not_applicable","reason":"No independent resolution refinement is meaningful for this sequential consistency check.","warnings":[]}

def _valid(status="valid"): return {"status":status,"rules":[]}
def _bench(passed=True): return {"passed":passed}
def _study(converged=True,applicable=True): return {"applicable":applicable,"converged":converged}

def test_all_deterministic_confidence_levels():
    assert confidence(_valid(),_bench(),_study())["level"]=="high"
    assert confidence(_valid("valid_with_warnings"),_bench(),_study())["level"]=="moderate"
    assert confidence(_valid(),None,_study())["level"]=="low"
    invalid={"status":"invalid","rules":[{"severity":"error","code":"OUTSIDE_VALIDITY_ENVELOPE"}]}
    assert confidence(invalid,_bench(),_study())["level"]=="invalid"

def test_warning_codes_are_stable_and_evidence_linked():
    finding=validate(THERMAL,{"length_m":1,"num_elements":2})["rules"][0]
    assert finding["code"]=="NEAR_VALIDITY_BOUNDARY"
    assert finding["evidence_reference"]=="normalized_inputs"

def test_persistence_is_idempotent_owned_and_restart_safe(tmp_path):
    path=str(tmp_path/"trust.db"); repo=EvidenceRepository(path)
    value={"record_type":"scientific_trust","status":"valid","experiment_id":None,"simulation_id":None,
           "parent_record_id":None,"payload":{"confidence":{"level":"high"},"evidence_references":["benchmark"]}}
    first=repo.create("user-a",value)
    assert repo.create("user-a",value)["id"]==first["id"]
    assert EvidenceRepository(path).get(first["id"],"user-a")["payload"]["confidence"]["level"]=="high"
    assert EvidenceRepository(path).get(first["id"],"user-b") is None

def test_api_serialization_and_user_b_denial(tmp_path,monkeypatch):
    monkeypatch.setenv("LOCAL_PERSISTENCE_DB_PATH",str(tmp_path/"api.db"))
    app.dependency_overrides[get_current_user]=lambda:{"id":"user-a"}
    client=TestClient(app)
    response=client.get("/api/v2/scientific/solvers/thermal_conduction_v1")
    assert response.status_code==200 and response.json()["physical_model"]=="Steady heat conduction"
    created=client.post("/api/v2/scientific/trust",json={
        "solver_id":"thermal_conduction_v1","inputs":{"length_m":1,"num_elements":20},
        "benchmark_inputs":{"cold_c":0,"hot_c":100},"computed_result":50,
        "convergence_values":[10,10.1,10.11]}).json()
    assert created["payload"]["confidence"]["level"]=="high"
    record_id=created["id"]
    app.dependency_overrides[get_current_user]=lambda:{"id":"user-b"}
    assert client.get(f"/api/v2/scientific/trust/{record_id}").status_code==404
    app.dependency_overrides.clear()
