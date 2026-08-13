from typing import Any
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from app.core.auth import get_current_user
from app.v2.execution import (
    ExecutionError,ExecutionService,InvalidTransition,NotFoundError,RESOURCE_POLICY,failure,
)
from app.v2.reasoning_reports import public_record

router=APIRouter(prefix="/api/v2/execution",tags=["Backend V2 - Reproducible Execution"])
class ManifestRequest(BaseModel): data:dict[str,Any]
class CloneRequest(BaseModel): changes:dict[str,Any]
class ReproduceRequest(BaseModel): idempotency_key:str=Field(min_length=1,max_length=128)
class ComparisonRequest(BaseModel): other_manifest_id:str;tolerances:dict[str,float]=Field(default_factory=dict)
class AttemptRequest(BaseModel): idempotency_key:str|None=None
class StartRunRequest(BaseModel): data:dict[str,Any];idempotency_key:str=Field(min_length=1,max_length=128)
class RetryRequest(BaseModel): idempotency_key:str=Field(min_length=1,max_length=128)
class CheckpointRequest(BaseModel): state:dict[str,Any];artifacts:list[dict[str,Any]]=Field(default_factory=list)

def _call(function,*args):
    try:return public_record(function(*args))
    except NotFoundError as exc:raise HTTPException(404,"Execution record not found") from exc
    except (ExecutionError,InvalidTransition) as exc:raise HTTPException(409,str(exc)) from exc

@router.get("/policy")
def policy(user:dict=Depends(get_current_user)):return {"version":"2.0","limits":RESOURCE_POLICY}
@router.post("/runs",status_code=201)
def start_run(payload:StartRunRequest,user:dict=Depends(get_current_user)):
    return _call(ExecutionService().start_run,user["id"],payload.data,payload.idempotency_key)
@router.post("/manifests",status_code=201)
def create_manifest(payload:ManifestRequest,user:dict=Depends(get_current_user)):
    return _call(ExecutionService().create_manifest,user["id"],payload.data)
@router.get("/manifests/{manifest_id}")
def get_manifest(manifest_id:str,user:dict=Depends(get_current_user)):
    return _call(ExecutionService().latest,manifest_id,user["id"],"run_manifest")
@router.post("/manifests/{manifest_id}/seal")
def seal_manifest(manifest_id:str,user:dict=Depends(get_current_user)):
    return _call(ExecutionService().seal,manifest_id,user["id"])
@router.post("/manifests/{manifest_id}/clone",status_code=201)
def clone_manifest(manifest_id:str,payload:CloneRequest,user:dict=Depends(get_current_user)):
    return _call(ExecutionService().clone,manifest_id,user["id"],payload.changes)
@router.post("/manifests/{manifest_id}/reproduce",status_code=201)
def reproduce_manifest(manifest_id:str,payload:ReproduceRequest,user:dict=Depends(get_current_user)):
    return _call(ExecutionService().reproduce,manifest_id,user["id"],payload.idempotency_key)
@router.post("/manifests/{manifest_id}/compare")
def compare_manifest(manifest_id:str,payload:ComparisonRequest,user:dict=Depends(get_current_user)):
    return _call(ExecutionService().compare,manifest_id,payload.other_manifest_id,user["id"],payload.tolerances)
@router.post("/manifests/{manifest_id}/bundle")
def generate_bundle(manifest_id:str,user:dict=Depends(get_current_user)):
    return _call(ExecutionService().bundle,manifest_id,user["id"])
@router.get("/manifests/{manifest_id}/artifacts")
def artifacts(manifest_id:str,user:dict=Depends(get_current_user)):
    record=_call(ExecutionService().latest,manifest_id,user["id"],"run_manifest")
    return {"artifacts":record["payload"].get("artifacts",[]),"bundle":record["payload"].get("bundle")}
@router.post("/manifests/{manifest_id}/attempts",status_code=201)
def create_attempt(manifest_id:str,payload:AttemptRequest,user:dict=Depends(get_current_user)):
    return _call(ExecutionService().create_attempt,manifest_id,user["id"],payload.idempotency_key)
@router.get("/attempts/{attempt_id}")
def get_attempt(attempt_id:str,user:dict=Depends(get_current_user)):
    return _call(ExecutionService().latest,attempt_id,user["id"],"job_attempt")
@router.get("/attempts/{attempt_id}/history")
def attempt_history(attempt_id:str,user:dict=Depends(get_current_user)):
    return _call(ExecutionService().history,attempt_id,user["id"],"job_attempt")
@router.post("/attempts/{attempt_id}/cancel")
def cancel_attempt(attempt_id:str,user:dict=Depends(get_current_user)):
    return _call(ExecutionService().cancel,attempt_id,user["id"])
@router.post("/attempts/{attempt_id}/retry",status_code=201)
def retry_attempt(attempt_id:str,payload:RetryRequest,user:dict=Depends(get_current_user)):
    return _call(ExecutionService().retry,attempt_id,user["id"],payload.idempotency_key)
@router.post("/attempts/{attempt_id}/checkpoint")
def checkpoint_attempt(attempt_id:str,payload:CheckpointRequest,user:dict=Depends(get_current_user)):
    return _call(ExecutionService().checkpoint,attempt_id,user["id"],payload.state,payload.artifacts)
@router.post("/attempts/{attempt_id}/resume")
def resume_attempt(attempt_id:str,user:dict=Depends(get_current_user)):
    return _call(ExecutionService().resume,attempt_id,user["id"])
@router.get("/failures/{category}")
def failure_information(category:str,user:dict=Depends(get_current_user)):return failure(category)
