from __future__ import annotations

import hashlib
import io
import json
import math
import re
import tempfile
import uuid
import zipfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.storage import FileStorage, build_simulation_object_key, get_storage
from app.v2.repository import EvidenceRepository
from app.v2.scientific_trust import REGISTRY, validate

POLICY_VERSION = "2.0"
RESOURCE_POLICY = {
    "max_design_variants": settings.MAX_BATCH_VARIANTS, "max_parameters": 100,
    "max_grid_size": 60, "max_convergence_levels": 3, "max_runtime_seconds": 3600,
    "max_attempts": 3, "max_artifacts": 50, "max_bundle_bytes": 25_000_000,
    "max_concurrent_owner_jobs": settings.MAX_CONCURRENT_SIMULATION_JOBS_PER_USER,
}
MANIFEST_TRANSITIONS = {
    "draft":{"sealed","superseded"},"sealed":{"executing","cancelled","superseded"},
    "executing":{"completed","failed","cancelled"},"completed":set(),"failed":set(),
    "cancelled":set(),"superseded":set(),
}
ATTEMPT_TRANSITIONS = {
    "queued":{"preparing","cancellation_requested","failed"},"preparing":{"validating_inputs","cancellation_requested","failed"},
    "validating_inputs":{"sealing_manifest","cancellation_requested","failed"},"sealing_manifest":{"preparing_solver","failed"},
    "preparing_solver":{"running_solver","cancellation_requested","failed"},"running_solver":{"checking_convergence","checkpointed","cancellation_requested","failed"},
    "checking_convergence":{"persisting_results","failed"},"persisting_results":{"completed","partially_completed","failed"},
    "checkpointed":{"retrying","cancellation_requested"},"retrying":{"preparing","failed"},
    "cancellation_requested":{"cancelled"},"completed":set(),"partially_completed":set(),"failed":{"retrying"},"cancelled":set(),
}
RESUMABLE_STAGES={"checkpointed","running_solver"}
FAILURES={
 "invalid_input":("INVALID_INPUT","Invalid input","Correct the highlighted input and clone the run.","non_retryable"),
 "invalid_geometry":("INVALID_GEOMETRY","Invalid geometry","Correct geometry parameters and clone the run.","non_retryable"),
 "unsupported_validity_envelope":("UNSUPPORTED_VALIDITY_ENVELOPE","Unsupported validity envelope","Move inputs into the supported envelope.","non_retryable"),
 "solver_non_convergence":("SOLVER_NON_CONVERGENCE","Solver did not converge","Review resolution and convergence settings.","conditionally_retryable"),
 "numerical_instability":("NUMERICAL_INSTABILITY","Numerical instability","Use safer bounded numerical settings.","conditionally_retryable"),
 "resource_limit_exceeded":("RESOURCE_LIMIT_EXCEEDED","Resource limit exceeded","Reduce the requested workload.","non_retryable"),
 "timeout":("EXECUTION_TIMEOUT","Execution timed out","Reduce resolution or retry once.","retryable"),
 "worker_lost":("WORKER_LOST","Worker was lost","Retry from the last verified checkpoint.","retryable"),
 "checkpoint_invalid":("CHECKPOINT_INVALID","Checkpoint is invalid","Restart from the sealed manifest.","non_retryable"),
 "artifact_integrity_failure":("ARTIFACT_INTEGRITY_FAILURE","Artifact integrity failed","Regenerate the affected artifact.","retryable"),
 "storage_failure":("STORAGE_FAILURE","Storage operation failed","Retry after storage recovers.","retryable"),
 "cancellation":("CANCELLED_BY_USER","Execution cancelled","Start a new attempt when ready.","non_retryable"),
 "internal_unexpected_failure":("INTERNAL_UNEXPECTED_FAILURE","Unexpected execution failure","Retry once or contact the operator.","conditionally_retryable"),
}
FORBIDDEN_KEYS=re.compile(r"(secret|token|password|database_url|broker_url|signed_url|private_key)",re.I)
ABSOLUTE_PATH=re.compile(r"(^[A-Za-z]:[\\/]|^/[^/])")


class ExecutionError(ValueError): pass
class NotFoundError(LookupError): pass
class InvalidTransition(ExecutionError): pass


def _now(): return datetime.now(timezone.utc).isoformat()


def normalize(value: Any) -> Any:
    if isinstance(value,dict):
        output={}
        for key in sorted(value):
            if FORBIDDEN_KEYS.search(str(key)): raise ExecutionError(f"Forbidden reproducibility field: {key}")
            output[str(key)]=normalize(value[key])
        return output
    if isinstance(value,(list,tuple)): return [normalize(x) for x in value]
    if isinstance(value,float):
        if not math.isfinite(value): raise ExecutionError("Non-finite numbers are not reproducible")
        result=float(format(value,".15g"))
        return 0.0 if result==0 else result
    if isinstance(value,str):
        if ABSOLUTE_PATH.search(value) or "X-Amz-Signature=" in value or "token=" in value.lower():
            raise ExecutionError("Internal paths and temporary URLs are forbidden")
    return value


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(normalize(value),sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()


def digest(value: Any) -> str: return hashlib.sha256(canonical_bytes(value)).hexdigest()


def checksum_set(data: dict[str,Any]) -> dict[str,str|None]:
    inputs={k:data.get(k) for k in ("design_parameters","material_properties","boundary_conditions",
        "mesh_configuration","convergence_configuration","normalized_scientific_inputs","random_seed")}
    execution={**inputs,"solver_id":data.get("solver_id"),"solver_version":data.get("solver_version"),
               "physical_model_id":data.get("physical_model_id"),"source_code_commit":data.get("source_code_commit")}
    return {"input_checksum":digest(inputs),"execution_checksum":digest(execution),
        "geometry_checksum":digest({"geometry_version":data.get("geometry_version"),"design_parameters":data.get("design_parameters")}),
        "result_checksum":digest(data["result_metrics"]) if data.get("result_metrics") is not None else None}


def failure(category: str, evidence_ids=None) -> dict[str,Any]:
    code,title,action,retryability=FAILURES.get(category,FAILURES["internal_unexpected_failure"])
    return {"category":category if category in FAILURES else "internal_unexpected_failure","code":code,
        "title":title,"explanation":title+".","retryability":retryability,
        "recommended_next_action":action,"related_evidence_ids":evidence_ids or [],"timestamp":_now()}


class ExecutionService:
    def __init__(self,repository:EvidenceRepository|None=None,storage:FileStorage|None=None,dispatcher=None):
        self.repo=repository or EvidenceRepository();self.storage=storage or get_storage()
        self.dispatcher=dispatcher or self._dispatch_simulation
    @staticmethod
    def _dispatch_simulation(request_data,user_id,idempotency_key):
        from app.module2_simulation.schemas import SimulationCreateRequest
        from app.module2_simulation.service import create_simulation_job_service
        try:request=SimulationCreateRequest.model_validate(request_data)
        except Exception as exc:raise ExecutionError("INVALID_INPUT: simulation request is invalid") from exc
        return create_simulation_job_service(request,user_id,idempotency_key).simulation_id
    def _get(self,record_id,user_id,kind):
        row=self.repo.get(record_id,user_id)
        if row is None or row["record_type"]!=kind:raise NotFoundError(record_id)
        return row
    def history(self,root_id,user_id,kind):
        root=self._get(root_id,user_id,kind);records=[root];frontier=[root["id"]]
        while frontier:
            children=[]
            for parent in frontier:children.extend(self.repo.list(user_id,kind,parent))
            records.extend(children);frontier=[x["id"] for x in children]
        return records
    def latest(self,root_id,user_id,kind): return self.history(root_id,user_id,kind)[-1]
    def create_manifest(self,user_id,data):
        normalized=normalize(data)
        if len(normalized.get("design_parameters",{}))>RESOURCE_POLICY["max_parameters"]:
            raise ExecutionError("RESOURCE_LIMIT_EXCEEDED: too many parameters")
        grid=normalized.get("mesh_configuration",{}).get("grid_size")
        if grid and grid>RESOURCE_POLICY["max_grid_size"]:raise ExecutionError("RESOURCE_LIMIT_EXCEEDED: grid too large")
        if len(normalized.get("artifacts",[]))>RESOURCE_POLICY["max_artifacts"]:
            raise ExecutionError("RESOURCE_LIMIT_EXCEEDED: too many artifacts")
        solver_id=normalized.get("solver_id")
        try:capability=REGISTRY.get(solver_id)
        except KeyError as exc:raise ExecutionError("Unsupported solver") from exc
        validity=validate(capability,normalized.get("normalized_scientific_inputs",{}))
        logical_id=str(uuid.uuid4())
        artifacts=[]
        for artifact in normalized.get("artifacts",[]):
            if not artifact.get("checksum_sha256") or not artifact.get("object_key"):
                raise ExecutionError("ARTIFACT_INTEGRITY_FAILURE: artifact metadata is incomplete")
            artifacts.append({**artifact,"owner_id":user_id,"producing_run":logical_id,
                "producing_attempt":normalized.get("attempt_id"),"created_at":artifact.get("created_at") or _now(),
                "integrity_status":artifact.get("integrity_status","unverified"),"private":True})
        payload={**normalized,**checksum_set(normalized),"manifest_id":logical_id,"owner_id":user_id,
            "status":"draft","created_at":_now(),"reproducibility_policy_version":POLICY_VERSION,
            "validity":validity,"artifacts":artifacts,"artifact_checksums":normalized.get("artifact_checksums",{}),
            "warnings":normalized.get("warnings",[])}
        value={"record_type":"run_manifest","status":"draft","experiment_id":normalized.get("experiment_id"),
            "simulation_id":normalized.get("job_id"),"parent_record_id":None,"payload":payload}
        return self.repo.create(user_id,value)
    def transition_manifest(self,manifest_id,user_id,status,updates=None):
        current=self.latest(manifest_id,user_id,"run_manifest");old=current["payload"]["status"]
        if status not in MANIFEST_TRANSITIONS.get(old,set()):raise InvalidTransition(f"{old} -> {status}")
        payload=deepcopy(current["payload"])
        if old!="draft" and updates:
            protected={"design_parameters","material_properties","boundary_conditions","mesh_configuration",
                       "convergence_configuration","normalized_scientific_inputs","solver_id","solver_version"}
            if protected & updates.keys():raise ExecutionError("Sealed scientific inputs are immutable; clone the run")
        payload.update(normalize(updates or {}));payload["status"]=status;payload[f"{status}_at"]=_now()
        payload.update(checksum_set(payload))
        return self.repo.create(user_id,{"record_type":"run_manifest","status":status,
            "experiment_id":current.get("experiment_id"),"simulation_id":current.get("simulation_id"),
            "parent_record_id":current["id"],"payload":payload})
    def seal(self,manifest_id,user_id):return self.transition_manifest(manifest_id,user_id,"sealed")
    def start_run(self,user_id,data,idempotency_key):
        request_data=data.get("simulation_request")
        if not request_data:raise ExecutionError("simulation_request is required to dispatch a supported run")
        root=self.create_manifest(user_id,data);self.seal(root["id"],user_id)
        job_id=self.dispatcher(request_data,user_id,idempotency_key)
        attempt=self.create_attempt(root["id"],user_id,f"run:{idempotency_key}")
        return self.transition_manifest(root["id"],user_id,"executing",{
            "job_id":job_id,"attempt_id":attempt["payload"]["attempt_id"],
            "dispatch_status":"dispatched","worker_task_id":attempt["payload"]["worker_task_id"]})
    def clone(self,manifest_id,user_id,changes,reproduction=False):
        current=self.latest(manifest_id,user_id,"run_manifest")
        allowed={"design_parameters","material_properties","boundary_conditions","mesh_configuration",
                 "convergence_configuration","random_seed"}
        if not set(changes)<=allowed:raise ExecutionError("Clone contains unsupported changes")
        data={k:v for k,v in current["payload"].items() if k not in {
            "manifest_id","owner_id","status","created_at","sealed_at","executing_at","completed_at",
            "input_checksum","execution_checksum","geometry_checksum","result_checksum","validity","confidence",
            "scientific_trust_evidence_id","attempt_id","result_metrics","artifacts","artifact_checksums","bundle"}}
        changed=[]
        for key,value in normalize(changes).items():
            changed.append({"field":key,"old_value":data.get(key),"new_value":value});data[key]=value
        request=deepcopy(data.get("simulation_request") or {})
        if "design_parameters" in changes:request["geometry"]={**request.get("geometry",{}),**changes["design_parameters"]}
        if "boundary_conditions" in changes:request["boundary_conditions"]={**request.get("boundary_conditions",{}),**changes["boundary_conditions"]}
        if "mesh_configuration" in changes:
            request["geometry"]={**request.get("geometry",{}),**changes["mesh_configuration"]}
        if "convergence_configuration" in changes:
            request["numerical_settings"]={**request.get("numerical_settings",{}),**changes["convergence_configuration"]}
        if request:data["simulation_request"]=request
        scientific=deepcopy(data.get("normalized_scientific_inputs",{}))
        for source in (changes.get("design_parameters",{}),changes.get("mesh_configuration",{})):
            scientific.update({k:v for k,v in source.items() if isinstance(v,(int,float))})
        data["normalized_scientific_inputs"]=scientific
        data["parent_manifest_id"]=current["payload"]["manifest_id"]
        data["original_manifest_id"]=current["payload"].get("original_manifest_id") or current["payload"]["manifest_id"]
        data["changed_fields"]=changed;data["reproduction_of"]=manifest_id if reproduction else None
        clone=self.create_manifest(user_id,data)
        return self.seal(clone["id"],user_id)
    def create_attempt(self,manifest_id,user_id,idempotency_key=None):
        manifest=self.latest(manifest_id,user_id,"run_manifest")
        if manifest["payload"]["status"] not in {"sealed","executing","failed"}:raise ExecutionError("Manifest is not executable")
        previous=[x for x in self.repo.list(user_id,"job_attempt") if x["payload"].get("manifest_id")==manifest["payload"]["manifest_id"]]
        if idempotency_key:
            match=next((x for x in previous if x["payload"].get("idempotency_key")==idempotency_key),None)
            if match:return match
        attempt_numbers={x["payload"].get("attempt_number") for x in previous}
        if len(attempt_numbers)>=RESOURCE_POLICY["max_attempts"]:raise ExecutionError("MAXIMUM_ATTEMPTS_EXCEEDED")
        logical=str(uuid.uuid4());number=len(attempt_numbers)+1
        payload={"attempt_id":logical,"manifest_id":manifest["payload"]["manifest_id"],"attempt_number":number,
            "worker_task_id":str(uuid.uuid4()),"stage":"queued","progress":0,"checkpoint":None,
            "outcome":None,"failure":None,"retryability":None,"cancellation_state":"none",
            "resource_usage_summary":{},"produced_evidence_ids":[],"produced_artifact_ids":[],
            "transition_history":[{"stage":"queued","timestamp":_now()}],"idempotency_key":idempotency_key,
            "started_at":None,"ended_at":None,"last_heartbeat":_now()}
        return self.repo.create(user_id,{"record_type":"job_attempt","status":"queued",
            "experiment_id":manifest.get("experiment_id"),"simulation_id":manifest.get("simulation_id"),
            "parent_record_id":None,"payload":payload})
    def transition_attempt(self,attempt_id,user_id,stage,updates=None):
        current=self.latest(attempt_id,user_id,"job_attempt");old=current["payload"]["stage"]
        if stage not in ATTEMPT_TRANSITIONS.get(old,set()):raise InvalidTransition(f"{old} -> {stage}")
        payload=deepcopy(current["payload"]);payload.update(normalize(updates or {}));payload["stage"]=stage
        payload["last_heartbeat"]=_now();payload["transition_history"].append({"stage":stage,"timestamp":_now()})
        if stage in {"preparing","retrying"} and not payload.get("started_at"):payload["started_at"]=_now()
        if stage in {"completed","partially_completed","failed","cancelled"}:payload["ended_at"]=_now();payload["outcome"]=stage
        return self.repo.create(user_id,{"record_type":"job_attempt","status":stage,
            "experiment_id":current.get("experiment_id"),"simulation_id":current.get("simulation_id"),
            "parent_record_id":current["id"],"payload":payload})
    def cancel(self,attempt_id,user_id):
        current=self.latest(attempt_id,user_id,"job_attempt")
        if current["payload"]["stage"] in {"completed","partially_completed","cancelled"}:return current
        if current["payload"]["stage"]=="cancellation_requested":return current
        requested=self.transition_attempt(attempt_id,user_id,"cancellation_requested",{"cancellation_state":"requested"})
        if current.get("simulation_id"):
            try:
                from app.module2_simulation.service import cancel_simulation_service
                cancel_simulation_service(current["simulation_id"],user_id)
            except Exception:
                pass
        return requested
    def retry(self,attempt_id,user_id,idempotency_key):
        current=self.latest(attempt_id,user_id,"job_attempt");fail=current["payload"].get("failure") or {}
        if current["payload"]["stage"]!="failed" or fail.get("retryability")=="non_retryable":
            raise ExecutionError("Attempt is not retryable")
        manifest_root=next(x["id"] for x in self.repo.list(user_id,"run_manifest")
            if x["payload"].get("manifest_id")==current["payload"]["manifest_id"] and x["parent_record_id"] is None)
        manifest=self.latest(manifest_root,user_id,"run_manifest")["payload"]
        request_data=manifest.get("simulation_request")
        if not request_data:raise ExecutionError("RETRY_INPUTS_MISSING")
        job_id=self.dispatcher(request_data,user_id,f"retry:{idempotency_key}")
        attempt=self.create_attempt(manifest_root,user_id,idempotency_key)
        payload=deepcopy(attempt["payload"]);payload["simulation_job_id"]=job_id;payload["dispatch_status"]="dispatched"
        return self.repo.create(user_id,{"record_type":"job_attempt","status":"queued",
            "experiment_id":attempt.get("experiment_id"),"simulation_id":job_id,
            "parent_record_id":attempt["id"],"payload":payload})
    def checkpoint(self,attempt_id,user_id,state,artifacts=None):
        current=self.latest(attempt_id,user_id,"job_attempt")
        checkpoint={"stage":current["payload"]["stage"],"state":normalize(state),"artifacts":normalize(artifacts or []),
            "version":"1.0","owner_id":user_id,"manifest_id":current["payload"]["manifest_id"],
            "attempt_id":current["payload"]["attempt_id"]}
        checkpoint["checksum"]=digest(checkpoint)
        return self.transition_attempt(attempt_id,user_id,"checkpointed",{"checkpoint":checkpoint})
    def resume(self,attempt_id,user_id):
        current=self.latest(attempt_id,user_id,"job_attempt");checkpoint=current["payload"].get("checkpoint")
        if not checkpoint:raise ExecutionError("CHECKPOINT_INVALID: checkpoint missing")
        supplied=checkpoint.get("checksum");check=dict(checkpoint);check.pop("checksum",None)
        if supplied!=digest(check):raise ExecutionError("CHECKPOINT_INVALID: checksum mismatch")
        if checkpoint.get("version")!="1.0":raise ExecutionError("CHECKPOINT_INVALID: version incompatible")
        if checkpoint.get("owner_id")!=user_id:raise NotFoundError(attempt_id)
        if current["payload"]["stage"] not in RESUMABLE_STAGES:raise ExecutionError("RESUME_STAGE_UNSUPPORTED")
        for artifact in checkpoint.get("artifacts",[]):
            if not self.storage.file_exists(artifact["object_key"]):raise ExecutionError("CHECKPOINT_ARTIFACT_MISSING")
            if artifact.get("checksum_sha256"):
                actual=hashlib.sha256(self.storage.open_bytes(artifact["object_key"])).hexdigest()
                if actual!=artifact["checksum_sha256"]:raise ExecutionError("CHECKPOINT_INVALID: artifact checksum mismatch")
        manifest=next((x["payload"] for x in self.repo.list(user_id,"run_manifest")
            if x["payload"].get("manifest_id")==current["payload"]["manifest_id"] and x["payload"].get("simulation_request")),None)
        if not manifest:raise ExecutionError("RESUME_INPUTS_MISSING")
        job_id=self.dispatcher(manifest["simulation_request"],user_id,f"resume:{current['payload']['attempt_id']}")
        return self.transition_attempt(attempt_id,user_id,"retrying",{
            "progress":current["payload"]["progress"],"simulation_job_id":job_id,"dispatch_status":"dispatched"})
    def compare(self,original_id,new_id,user_id,tolerances=None):
        a=self.latest(original_id,user_id,"run_manifest")["payload"];b=self.latest(new_id,user_id,"run_manifest")["payload"]
        if a.get("solver_id")!=b.get("solver_id") or a.get("solver_version")!=b.get("solver_version") or a.get("physical_model_id")!=b.get("physical_model_id"):
            result={"status":"not_comparable","reason_codes":["INCOMPATIBLE_EXECUTION_MODEL"],"metrics":[]}
            return self._store_comparison(new_id,user_id,result,a,b)
        metrics=[];tolerances=tolerances or {}
        left=a.get("result_metrics") or {};right=b.get("result_metrics") or {}
        for name in sorted(set(left)&set(right)):
            if not isinstance(left[name],(int,float)) or not isinstance(right[name],(int,float)):continue
            absolute=abs(right[name]-left[name]);relative=absolute/max(abs(left[name]),1e-15);tol=tolerances.get(name,0)
            metrics.append({"metric_name":name,"original_value":left[name],"new_value":right[name],
                "absolute_difference":absolute,"relative_difference":relative,"declared_tolerance":tol,
                "comparison_status":"exact_match" if absolute==0 else ("within_tolerance" if relative<=tol else "outside_tolerance"),
                "reason_code":"EXACT_VALUE" if absolute==0 else ("WITHIN_DECLARED_TOLERANCE" if relative<=tol else "OUTSIDE_DECLARED_TOLERANCE")})
        if not metrics:
            return self._store_comparison(new_id,user_id,{"status":"not_comparable","reason_codes":["COMPARISON_METRIC_UNAVAILABLE"],"metrics":[]},a,b)
        statuses={x["comparison_status"] for x in metrics}
        overall="outside_tolerance" if "outside_tolerance" in statuses else ("within_tolerance" if "within_tolerance" in statuses else "exact_match")
        result={"status":overall,"reason_codes":[],"metrics":metrics,
            "input_equivalent":a.get("input_checksum")==b.get("input_checksum"),
            "geometry_equivalent":a.get("geometry_checksum")==b.get("geometry_checksum")}
        return self._store_comparison(new_id,user_id,result,a,b)
    def _store_comparison(self,new_id,user_id,result,original,new):
        current=self.latest(new_id,user_id,"run_manifest");payload=deepcopy(current["payload"])
        result={**result,"original_manifest_id":original["manifest_id"],"new_manifest_id":new["manifest_id"],
                "compared_at":_now(),"comparison_checksum":digest(result)}
        payload["comparison"]=result
        record=self.repo.create(user_id,{"record_type":"run_manifest","status":current["status"],
            "experiment_id":current.get("experiment_id"),"simulation_id":current.get("simulation_id"),
            "parent_record_id":current["id"],"payload":payload})
        return {**result,"evidence_record_id":record["id"]}
    def reproduce(self,manifest_id,user_id,idempotency_key):
        original=self.latest(manifest_id,user_id,"run_manifest")
        if original["payload"]["status"] not in {"sealed","completed","failed"}:raise ExecutionError("Original manifest is not sealed")
        existing=next((x for x in self.repo.list(user_id,"run_manifest")
            if x["payload"].get("reproduction_key")==idempotency_key and x["payload"].get("reproduction_of")==manifest_id),None)
        if existing:return self.latest(existing["id"],user_id,"run_manifest")
        clone=self.clone(manifest_id,user_id,{},True)
        clone_payload=deepcopy(clone["payload"]);clone_payload["reproduction_key"]=idempotency_key
        reproduction=self.repo.create(user_id,{"record_type":"run_manifest","status":"sealed",
            "experiment_id":clone.get("experiment_id"),"simulation_id":clone.get("simulation_id"),
            "parent_record_id":clone["id"],"payload":clone_payload})
        attempt=self.create_attempt(reproduction["id"],user_id,f"reproduce:{idempotency_key}")
        request_data=reproduction["payload"].get("simulation_request")
        if not request_data:raise ExecutionError("REPRODUCTION_INPUTS_MISSING")
        job_id=self.dispatcher(request_data,user_id,f"reproduce:{idempotency_key}")
        final_payload=deepcopy(reproduction["payload"]);final_payload["attempt_id"]=attempt["payload"]["attempt_id"]
        final_payload["job_id"]=job_id;final_payload["dispatch_status"]="dispatched"
        return self.repo.create(user_id,{"record_type":"run_manifest","status":"sealed",
            "experiment_id":reproduction.get("experiment_id"),"simulation_id":reproduction.get("simulation_id"),
            "parent_record_id":reproduction["id"],"payload":final_payload})
    def bundle(self,manifest_id,user_id):
        manifest=self.latest(manifest_id,user_id,"run_manifest");payload=manifest["payload"]
        existing=payload.get("bundle")
        if existing and self.storage.file_exists(existing["object_key"]):return existing
        files={
            "manifest.json":canonical_bytes(payload),
            "normalized-input.json":canonical_bytes(payload.get("normalized_scientific_inputs",{})),
            "geometry.json":canonical_bytes(payload.get("design_parameters",{})),
            "material.json":canonical_bytes(payload.get("material_properties",{})),
            "boundary-conditions.json":canonical_bytes(payload.get("boundary_conditions",{})),
            "solver-metadata.json":canonical_bytes({"solver_id":payload.get("solver_id"),"version":payload.get("solver_version"),"physical_model_id":payload.get("physical_model_id")}),
            "scientific-trust.json":canonical_bytes({"evidence_id":payload.get("scientific_trust_evidence_id"),"confidence":payload.get("confidence"),"warnings":payload.get("warnings",[])}),
            "result-metrics.json":canonical_bytes(payload.get("result_metrics",{})),
            "artifact-inventory.json":canonical_bytes(payload.get("artifacts",[])),
            "lineage.json":canonical_bytes({k:payload.get(k) for k in ("parent_manifest_id","original_manifest_id","originating_decision","originating_iteration")}),
            "README.txt":b"ASRE-Lab reproducibility bundle. Recreate the normalized inputs with the declared solver version. Numerical equality may require declared tolerances.\n",
        }
        for index,artifact in enumerate(payload.get("artifacts",[])):
            key=artifact.get("object_key","")
            if not key.startswith(f"users/{user_id}/"):raise ExecutionError("ARTIFACT_INTEGRITY_FAILURE: owner namespace mismatch")
            if not self.storage.file_exists(key):raise ExecutionError("ARTIFACT_INTEGRITY_FAILURE: artifact missing")
            content=self.storage.open_bytes(key);actual=hashlib.sha256(content).hexdigest()
            if artifact.get("checksum_sha256")!=actual:raise ExecutionError("ARTIFACT_INTEGRITY_FAILURE: checksum mismatch")
            suffix=Path(key).suffix.lower()
            if suffix not in {".step",".stp",".stl",".npz",".json",".csv"}:suffix=".bin"
            files[f"artifacts/artifact-{index:03d}{suffix}"]=content
        checks={name:hashlib.sha256(content).hexdigest() for name,content in files.items()}
        files["checksums.json"]=canonical_bytes(checks)
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,"w",zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(files):
                info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
                archive.writestr(info,files[name])
        data=stream.getvalue()
        if len(data)>RESOURCE_POLICY["max_bundle_bytes"]:raise ExecutionError("RESOURCE_LIMIT_EXCEEDED: bundle too large")
        experiment=str(payload.get("experiment_id") or "standalone");logical=payload["manifest_id"]
        key=build_simulation_object_key(user_id,experiment,logical,"reproducibility.zip")
        with tempfile.NamedTemporaryFile(delete=False,suffix=".zip") as temp:temp.write(data);path=Path(temp.name)
        try:self.storage.save_file(key,path)
        finally:path.unlink(missing_ok=True)
        bundle={"object_key":key,"checksum_sha256":hashlib.sha256(data).hexdigest(),"byte_size":len(data),
            "content_type":"application/zip","integrity_status":"verified","private":True,
            "producing_run":logical,"created_at":_now(),"inventory":sorted(files)}
        updated=deepcopy(payload);updated["bundle"]=bundle
        self.repo.create(user_id,{"record_type":"run_manifest","status":manifest["status"],
            "experiment_id":manifest.get("experiment_id"),"simulation_id":manifest.get("simulation_id"),
            "parent_record_id":manifest["id"],"payload":updated})
        return bundle
