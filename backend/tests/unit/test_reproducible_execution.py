import hashlib
import io
import zipfile
import pytest
from fastapi.testclient import TestClient
from app.core.auth import get_current_user
from app.core.storage import LocalFileStorage
from app.main import app
from app.v2.execution import (
    ExecutionError,ExecutionService,InvalidTransition,RESOURCE_POLICY,
    canonical_bytes,checksum_set,digest,failure,normalize,
)
from app.v2.repository import EvidenceRepository

def data(**updates):
    value={"experiment_id":"exp-1","design_id":"design-1","geometry_version":"1",
        "design_parameters":{"length_m":1.0,"height_m":.1},"material_properties":{"k":2.0},
        "boundary_conditions":{"cold_c":0.0,"hot_c":100.0},"solver_id":"thermal_conduction_v1",
        "solver_version":"1.0.0","physical_model_id":"steady_heat_conduction",
        "mesh_configuration":{"grid_size":20},"convergence_configuration":{"levels":3},
        "normalized_scientific_inputs":{"length_m":1.0,"num_elements":20},"random_seed":None,
        "source_code_commit":"abc123","source_application_version":"2.0",
        "scientific_trust_evidence_id":"trust-1","benchmark_evidence_id":"benchmark",
        "convergence_evidence_id":"convergence","confidence":{"level":"high"},"warnings":[],
        "result_metrics":{"max_temperature_c":100.0},"artifacts":[],
        "simulation_request":{"solver_id":"thermal_conduction_v1","experiment_id":"exp-1","design_id":"design-1",
            "material":{"name":"steel"},"geometry":{"dimension":"1d","length_m":1.0,"num_elements":20},
            "boundary_conditions":{"ambient_temperature_c":0.0,"prescribed_temperature_c":100.0},
            "initial_conditions":{},"numerical_settings":{"max_iterations":300,"tolerance":1e-5}}}
    value.update(updates);return value

@pytest.fixture
def service(tmp_path):
    dispatcher=lambda request,user,key:"job-"+hashlib.sha256(f"{user}:{key}".encode()).hexdigest()[:12]
    return ExecutionService(EvidenceRepository(str(tmp_path/"records.db")),LocalFileStorage(tmp_path/"objects"),dispatcher)

def create_sealed(service,user="a",**updates):
    root=service.create_manifest(user,data(**updates))
    return root,service.seal(root["id"],user)

def test_canonicalization_and_checksums_are_stable():
    left={"b":-0.0,"a":{"x":1.0000000000000002},"optional":None}
    right={"optional":None,"a":{"x":1.0},"b":0.0}
    assert canonical_bytes(left)==canonical_bytes(right)
    assert digest(left)==digest(right) and len(digest(left))==64
    checks=checksum_set(data())
    assert checks["input_checksum"] and checks["geometry_checksum"] and checks["result_checksum"]

@pytest.mark.parametrize("bad",[
    {"access_token":"secret"},{"path":"C:\\private\\file"},{"url":"https://x?X-Amz-Signature=abc"},
    {"value":float("nan")},
])
def test_canonicalization_rejects_secrets_paths_urls_and_nonfinite(bad):
    with pytest.raises(ExecutionError):normalize(bad)

def test_manifest_sealing_immutability_lineage_and_restart(service):
    root,sealed=create_sealed(service)
    assert sealed["payload"]["status"]=="sealed"
    with pytest.raises(ExecutionError):
        service.transition_manifest(root["id"],"a","executing",{"design_parameters":{"length_m":2}})
    reloaded=ExecutionService(EvidenceRepository(service.repo.path),service.storage,service.dispatcher)
    assert reloaded.latest(root["id"],"a","run_manifest")["id"]==sealed["id"]
    with pytest.raises(Exception):reloaded.latest(root["id"],"b","run_manifest")

def test_invalid_manifest_transition_rejected(service):
    root=service.create_manifest("a",data())
    with pytest.raises(InvalidTransition):service.transition_manifest(root["id"],"a","completed")

def test_clone_records_changes_rechecks_validity_and_checksum(service):
    root,sealed=create_sealed(service)
    child=service.clone(root["id"],"a",{"design_parameters":{"length_m":2},"boundary_conditions":{"cold_c":5,"hot_c":90}})
    assert child["payload"]["parent_manifest_id"]==root["payload"]["manifest_id"]
    assert child["payload"]["original_manifest_id"]==root["payload"]["manifest_id"]
    assert {x["field"] for x in child["payload"]["changed_fields"]}=={"design_parameters","boundary_conditions"}
    assert child["payload"]["input_checksum"]!=sealed["payload"]["input_checksum"]
    assert "confidence" not in child["payload"] and "result_metrics" not in child["payload"]
    assert "validity" in child["payload"]

def test_reproduction_is_linked_and_idempotent(service):
    root,_=create_sealed(service)
    first=service.reproduce(root["id"],"a","same-key")
    second=service.reproduce(root["id"],"a","same-key")
    assert first["id"]==second["id"]
    assert first["payload"]["original_manifest_id"]==root["payload"]["manifest_id"]

def test_comparison_exact_tolerance_outside_and_not_comparable(service):
    a,_=create_sealed(service)
    b,_=create_sealed(service)
    assert service.compare(a["id"],b["id"],"a")["status"]=="exact_match"
    c,_=create_sealed(service,result_metrics={"max_temperature_c":100.5})
    assert service.compare(a["id"],c["id"],"a",{"max_temperature_c":.01})["status"]=="within_tolerance"
    assert service.compare(a["id"],c["id"],"a",{"max_temperature_c":.001})["status"]=="outside_tolerance"
    d,_=create_sealed(service,solver_version="2.0")
    assert service.compare(a["id"],d["id"],"a")["status"]=="not_comparable"

def test_bundle_is_deterministic_private_owned_and_restart_safe(service):
    root,_=create_sealed(service)
    first=service.bundle(root["id"],"a");second=service.bundle(root["id"],"a")
    assert first==second and first["private"] and first["integrity_status"]=="verified"
    raw=service.storage.open_bytes(first["object_key"])
    assert hashlib.sha256(raw).hexdigest()==first["checksum_sha256"]
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names=archive.namelist();content=b"".join(archive.read(x) for x in names)
    assert "checksums.json" in names and "manifest.json" in names and b"C:\\Users\\" not in content
    reloaded=ExecutionService(EvidenceRepository(service.repo.path),service.storage,service.dispatcher)
    assert reloaded.bundle(root["id"],"a")==first
    with pytest.raises(Exception):reloaded.bundle(root["id"],"b")

def test_bundle_embeds_only_checksum_verified_private_artifacts(service,tmp_path):
    content=b"solid bounded\nendsolid bounded\n";source=tmp_path/"model.stl";source.write_bytes(content)
    key="users/a/experiments/exp-1/simulations/run-artifact/model.stl";service.storage.save_file(key,source)
    root,_=create_sealed(service,artifacts=[{"object_key":key,"checksum_sha256":hashlib.sha256(content).hexdigest(),
        "artifact_type":"stl","byte_size":len(content),"content_type":"model/stl"}])
    bundle=service.bundle(root["id"],"a")
    with zipfile.ZipFile(io.BytesIO(service.storage.open_bytes(bundle["object_key"]))) as archive:
        assert archive.read("artifacts/artifact-000.stl")==content
    bad,_=create_sealed(service,artifacts=[{"object_key":key,"checksum_sha256":"0"*64}])
    with pytest.raises(ExecutionError,match="checksum mismatch"):service.bundle(bad["id"],"a")

def test_attempt_transitions_history_and_restart(service):
    root,_=create_sealed(service)
    attempt=service.create_attempt(root["id"],"a","dispatch")
    again=service.create_attempt(root["id"],"a","dispatch")
    assert attempt["id"]==again["id"] and attempt["payload"]["attempt_number"]==1
    preparing=service.transition_attempt(attempt["id"],"a","preparing",{"progress":5})
    assert preparing["payload"]["progress"]==5
    reloaded=ExecutionService(EvidenceRepository(service.repo.path),service.storage)
    assert len(reloaded.history(attempt["id"],"a","job_attempt"))==2
    with pytest.raises(InvalidTransition):service.transition_attempt(attempt["id"],"a","completed")

def advance_running(service,attempt_id):
    for stage in ("preparing","validating_inputs","sealing_manifest","preparing_solver","running_solver"):
        service.transition_attempt(attempt_id,"a",stage)

def test_cancellation_idempotency_completed_behavior_and_owner_denial(service):
    root,_=create_sealed(service);attempt=service.create_attempt(root["id"],"a")
    requested=service.cancel(attempt["id"],"a");again=service.cancel(attempt["id"],"a")
    assert requested["id"]==again["id"] and requested["payload"]["cancellation_state"]=="requested"
    cancelled=service.transition_attempt(attempt["id"],"a","cancelled")
    assert service.cancel(attempt["id"],"a")["id"]==cancelled["id"]
    with pytest.raises(Exception):service.cancel(attempt["id"],"b")

def test_checkpoint_resume_integrity_stage_artifact_and_version(service,tmp_path):
    root,_=create_sealed(service);attempt=service.create_attempt(root["id"],"a");advance_running(service,attempt["id"])
    checkpointed=service.checkpoint(attempt["id"],"a",{"completed_step":2})
    resumed=service.resume(attempt["id"],"a")
    assert resumed["payload"]["stage"]=="retrying"
    broken=checkpointed["payload"];broken["checkpoint"]["checksum"]="0"*64
    corrupt=service.repo.create("a",{"record_type":"job_attempt","status":"checkpointed","experiment_id":"exp-1",
        "simulation_id":None,"parent_record_id":checkpointed["id"],"payload":broken})
    with pytest.raises(ExecutionError):service.resume(attempt["id"],"a")

def test_resume_rejects_missing_artifact_and_unsupported_stage(service):
    root,_=create_sealed(service);attempt=service.create_attempt(root["id"],"a")
    with pytest.raises(ExecutionError,match="checkpoint missing"):service.resume(attempt["id"],"a")
    advance_running(service,attempt["id"])
    service.checkpoint(attempt["id"],"a",{},[{"object_key":"users/a/experiments/exp-1/simulations/x/missing.npz","checksum_sha256":"0"*64}])
    with pytest.raises(ExecutionError,match="ARTIFACT_MISSING"):service.resume(attempt["id"],"a")

def test_retry_failure_classification_attempt_increment_and_idempotency(service):
    root,_=create_sealed(service);attempt=service.create_attempt(root["id"],"a")
    service.transition_attempt(attempt["id"],"a","failed",{"failure":failure("worker_lost")})
    retry=service.retry(attempt["id"],"a","retry-1");again=service.retry(attempt["id"],"a","retry-1")
    assert retry["id"]==again["id"] and retry["payload"]["attempt_number"]==2
    nonretry=service.create_attempt(root["id"],"a","other")
    service.transition_attempt(nonretry["id"],"a","failed",{"failure":failure("invalid_input")})
    with pytest.raises(ExecutionError):service.retry(nonretry["id"],"a","no")

def test_resource_limits_and_failure_safety(service):
    too_large=data(mesh_configuration={"grid_size":RESOURCE_POLICY["max_grid_size"]+1})
    with pytest.raises(ExecutionError,match="RESOURCE_LIMIT_EXCEEDED"):service.create_manifest("a",too_large)
    record=failure("internal_unexpected_failure")
    rendered=str(record)
    assert "Traceback" not in rendered and "C:\\Users\\" not in rendered
    assert failure("timeout")["retryability"]=="retryable"

def test_maximum_attempts_is_enforced(service):
    root,_=create_sealed(service)
    for index in range(RESOURCE_POLICY["max_attempts"]):
        service.create_attempt(root["id"],"a",f"attempt-{index}")
    with pytest.raises(ExecutionError,match="MAXIMUM_ATTEMPTS"):service.create_attempt(root["id"],"a","too-many")

def test_api_owner_isolation_and_serialization(tmp_path,monkeypatch):
    monkeypatch.setenv("LOCAL_PERSISTENCE_DB_PATH",str(tmp_path/"api.db"))
    monkeypatch.setenv("LOCAL_STORAGE_ROOT",str(tmp_path/"storage"))
    app.dependency_overrides[get_current_user]=lambda:{"id":"a"}
    client=TestClient(app)
    created=client.post("/api/v2/execution/manifests",json={"data":data()})
    assert created.status_code==201
    root=created.json()["id"]
    assert client.post(f"/api/v2/execution/manifests/{root}/seal").status_code==200
    assert client.get("/api/v2/execution/policy").json()["limits"]["max_attempts"]==3
    app.dependency_overrides[get_current_user]=lambda:{"id":"b"}
    assert client.get(f"/api/v2/execution/manifests/{root}").status_code==404
    app.dependency_overrides.clear()
