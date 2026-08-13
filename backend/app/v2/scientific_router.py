from typing import Any
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from app.core.auth import get_current_user
from app.v2.repository import EvidenceRepository
from app.core.repository import get_repository
from app.module2_simulation.source_resolution import (
    SimulationSourceError,
    SimulationSourceNotFoundError,
    resolve_simulation_source,
)
from app.v2.scientific_trust import REGISTRY,benchmark,convergence,metadata,reference_only,validate
from app.v2.refinement import create_refinement_evidence
from app.v2.trust_v2 import derive_trust_record
from app.v2.evidence_integrity import records_by_type
from app.v2.evidence_models import EvidenceType
from app.v2.claim_integrity import classify_claim

router=APIRouter(prefix="/api/v2/scientific",tags=["Backend V2 - Scientific Trust"])
class Inputs(BaseModel): inputs:dict[str,Any]
class BenchmarkRequest(BaseModel): inputs:dict[str,float];computed_result:float|None=None;source_simulation_id:str|None=None
class ConvergenceRequest(BaseModel):
    values:list[float]=Field(min_length=3,max_length=3)
    configurations:list[dict]=Field(default_factory=lambda:[{},{},{}],min_length=3,max_length=3)
    threshold:float=Field(default=.02,gt=0,le=1)
class TrustRequest(BaseModel):
    solver_id:str;inputs:dict[str,Any];benchmark_inputs:dict[str,float]
    computed_result:float|None=None;source_simulation_id:str|None=None;convergence_values:list[float]|None=None
    experiment_id:str|None=None;simulation_id:str|None=None
class RefinementRequest(BaseModel):
    simulation_ids:list[str]=Field(min_length=3,max_length=3)
    selected_metric:str=Field(min_length=1)
    refinement_parameter:str=Field(min_length=1)
    threshold:float=Field(default=.02,gt=0,le=1)
def _item(solver_id):
    try:return REGISTRY.get(solver_id)
    except KeyError:raise HTTPException(404,"Scientific solver capability not found")
def _authoritative_benchmark(item, source_simulation_id, user_id, client_value=None):
    try:
        source=resolve_simulation_source(
            source_simulation_id,
            user_id,
            require_completed_result=True,
            required_summary_metric=item.benchmark_metric,
        )
    except SimulationSourceNotFoundError:
        raise HTTPException(404,"Source simulation not found")
    except SimulationSourceError as exc:
        raise HTTPException(422,str(exc))
    if source.solver_id != item.solver_id: raise HTTPException(422,"Source simulation solver does not match benchmark solver")
    result=source.result
    computed=float(result.summary_metrics[item.benchmark_metric])
    if client_value is not None and float(client_value) != computed: raise HTTPException(422,"Client computed_result does not match persisted scientific result")
    return computed,source
@router.get("/solvers")
def solvers(user:dict=Depends(get_current_user)):return [metadata(x) for x in REGISTRY.list()]
@router.get("/solvers/{solver_id}")
def solver(solver_id:str,user:dict=Depends(get_current_user)):return metadata(_item(solver_id))
@router.post("/solvers/{solver_id}/validate")
def validate_inputs(solver_id:str,payload:Inputs,user:dict=Depends(get_current_user)):return validate(_item(solver_id),payload.inputs)
@router.post("/solvers/{solver_id}/benchmark")
def execute_benchmark(solver_id:str,payload:BenchmarkRequest,user:dict=Depends(get_current_user)):
    if payload.source_simulation_id is None or payload.computed_result is None: raise HTTPException(422,"source_simulation_id and computed_result are required for authoritative benchmark evidence")
    item=_item(solver_id); computed,source=_authoritative_benchmark(item,payload.source_simulation_id,user["id"],payload.computed_result)
    try:
        result=benchmark(item,payload.inputs,computed,payload.source_simulation_id)
        core_repo=get_repository(); evidence_repo=EvidenceRepository(repository=core_repo)
        numerical=records_by_type(evidence_repo,user["id"],source).get(EvidenceType.NUMERICAL_RESULT,[])
        source_ids=[sorted(numerical,key=lambda x:(x[0].get("created_at",""),x[0]["id"]))[-1][0]["id"]] if numerical else []
        persisted=evidence_repo.create_scientific_evidence(user["id"],{
            "evidence_type":"benchmark","schema_version":"2.0",
            "experiment_id":source.experiment_id,"design_id":source.design_id,
            "simulation_id":source.simulation_id,"solver_id":source.solver_id,
            "solver_version":source.solver_version,
            "input_fingerprint":source.result.validation_metadata.get("input_fingerprint"),
            "result_hash":source.result.reproducibility_hash or None,
            "source_ids":source_ids,"status":"pass" if result["passed"] else "fail",
            "benchmark_id":result["benchmark_id"],"metric_name":result["selected_metric"],
            "computed_value":result["computed_result"],"reference_value":result["reference_result"],
            "absolute_error":result["absolute_error"],"relative_error":result["relative_error"],
            "tolerance":result["declared_tolerance"],"passed":result["passed"],
            "source_simulation_id":source.simulation_id,"limitations":result["limitations"],
        })
        return {**result,"evidence_id":persisted["id"]}
    except ValueError as exc:raise HTTPException(422,str(exc))
@router.post("/solvers/{solver_id}/reference-only")
def execute_reference_only(solver_id:str,payload:Inputs,user:dict=Depends(get_current_user)):
    return reference_only(_item(solver_id),payload.inputs)
@router.post("/solvers/{solver_id}/convergence")
def execute_convergence(solver_id:str,payload:ConvergenceRequest,user:dict=Depends(get_current_user)):
    return {**convergence(_item(solver_id),payload.values,payload.configurations,payload.threshold),
            "authoritative":False,"evidence_id":None}
@router.post("/solvers/{solver_id}/refinement",status_code=201)
def execute_refinement(solver_id:str,payload:RefinementRequest,user:dict=Depends(get_current_user)):
    _item(solver_id)
    repo=get_repository()
    try:
        first=resolve_simulation_source(payload.simulation_ids[0],user["id"],require_result=True,repository=repo)
        if first.solver_id!=solver_id:raise ValueError("Refinement source solver does not match route solver")
        record=create_refinement_evidence(
            user["id"],payload.simulation_ids,payload.selected_metric,
            payload.refinement_parameter,payload.threshold,repository=repo,
        )
    except SimulationSourceNotFoundError:raise HTTPException(404,"Refinement source simulation not found")
    except (SimulationSourceError,ValueError) as exc:raise HTTPException(422,str(exc))
    return record
@router.post("/trust",status_code=201)
def create_trust(payload:TrustRequest,user:dict=Depends(get_current_user)):
    _item(payload.solver_id)
    simulation_id=payload.simulation_id or payload.source_simulation_id
    if simulation_id is None:raise HTTPException(422,"simulation_id is required for persisted ScientificTrustRecord V2")
    try:
        source=resolve_simulation_source(simulation_id,user["id"],require_result=True,repository=get_repository())
        if source.solver_id!=payload.solver_id:raise ValueError("Source simulation solver does not match trust solver")
        record=derive_trust_record(user["id"],simulation_id,repository=get_repository())
    except SimulationSourceNotFoundError:raise HTTPException(404,"Source simulation not found")
    except (SimulationSourceError,ValueError) as exc:raise HTTPException(422,str(exc))
    return record
@router.post("/trust/simulations/{simulation_id}",status_code=201)
def create_trust_for_simulation(simulation_id:str,user:dict=Depends(get_current_user)):
    try:return derive_trust_record(user["id"],simulation_id,repository=get_repository())
    except SimulationSourceNotFoundError:raise HTTPException(404,"Source simulation not found")
    except (SimulationSourceError,ValueError) as exc:raise HTTPException(422,str(exc))
@router.get("/trust/{record_id}")
def get_trust(record_id:str,user:dict=Depends(get_current_user)):
    evidence=EvidenceRepository(repository=get_repository())
    row=evidence.get(record_id,user["id"])
    if row is None or row["record_type"]!="scientific_trust":raise HTTPException(404,"Scientific trust record not found")
    if classify_claim(evidence,user["id"],"Scientific trust classification",[record_id])["classification"]!="finding":
        raise HTTPException(422,"Scientific trust record provenance is invalid or legacy")
    return row
