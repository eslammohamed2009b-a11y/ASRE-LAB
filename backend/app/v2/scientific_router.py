from typing import Any
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from app.core.auth import get_current_user
from app.v2.repository import EvidenceRepository
from app.v2.scientific_trust import REGISTRY,benchmark,confidence,convergence,metadata,validate

router=APIRouter(prefix="/api/v2/scientific",tags=["Backend V2 - Scientific Trust"])
class Inputs(BaseModel): inputs:dict[str,Any]
class BenchmarkRequest(BaseModel): inputs:dict[str,float];computed_result:float|None=None
class ConvergenceRequest(BaseModel):
    values:list[float]=Field(min_length=3,max_length=3)
    configurations:list[dict]=Field(default_factory=lambda:[{},{},{}],min_length=3,max_length=3)
    threshold:float=Field(default=.02,gt=0,le=1)
class TrustRequest(BaseModel):
    solver_id:str;inputs:dict[str,Any];benchmark_inputs:dict[str,float]
    computed_result:float|None=None;convergence_values:list[float]|None=None
    experiment_id:str|None=None;simulation_id:str|None=None
def _item(solver_id):
    try:return REGISTRY.get(solver_id)
    except KeyError:raise HTTPException(404,"Scientific solver capability not found")
@router.get("/solvers")
def solvers(user:dict=Depends(get_current_user)):return [metadata(x) for x in REGISTRY.list()]
@router.get("/solvers/{solver_id}")
def solver(solver_id:str,user:dict=Depends(get_current_user)):return metadata(_item(solver_id))
@router.post("/solvers/{solver_id}/validate")
def validate_inputs(solver_id:str,payload:Inputs,user:dict=Depends(get_current_user)):return validate(_item(solver_id),payload.inputs)
@router.post("/solvers/{solver_id}/benchmark")
def execute_benchmark(solver_id:str,payload:BenchmarkRequest,user:dict=Depends(get_current_user)):
    return benchmark(_item(solver_id),payload.inputs,payload.computed_result)
@router.post("/solvers/{solver_id}/convergence")
def execute_convergence(solver_id:str,payload:ConvergenceRequest,user:dict=Depends(get_current_user)):
    return convergence(_item(solver_id),payload.values,payload.configurations,payload.threshold)
@router.post("/trust",status_code=201)
def create_trust(payload:TrustRequest,user:dict=Depends(get_current_user)):
    item=_item(payload.solver_id);validity=validate(item,payload.inputs)
    bench=benchmark(item,payload.benchmark_inputs,payload.computed_result)
    study=convergence(item,payload.convergence_values or [1,1,1])
    warnings=list(validity["rules"])+list(study.get("warnings",[]))
    result={"solver":metadata(item),"validity":validity,"benchmark":bench,"convergence":study,
            "warnings":warnings,"confidence":confidence(validity,bench,study,warnings),
            "evidence_references":["validity","benchmark","convergence"],"limitations":metadata(item)["limitations"]}
    value={"record_type":"scientific_trust","status":validity["status"],"experiment_id":payload.experiment_id,
           "simulation_id":payload.simulation_id,"parent_record_id":None,"payload":result}
    return EvidenceRepository().create(user["id"],value)
@router.get("/trust/{record_id}")
def get_trust(record_id:str,user:dict=Depends(get_current_user)):
    row=EvidenceRepository().get(record_id,user["id"])
    if row is None or row["record_type"]!="scientific_trust":raise HTTPException(404,"Scientific trust record not found")
    return row
