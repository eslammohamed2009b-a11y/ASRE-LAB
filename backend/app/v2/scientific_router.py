from typing import Any,Literal
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from app.core.auth import get_current_user
from app.v2.repository import EvidenceRepository
from app.core.repository import get_repository
from app.core.storage import get_storage
from app.module2_simulation.geometry_physics_router import _load_mesh
from app.module2_simulation.thermal_field_benchmark import (
    LINEAR_BENCHMARK_ID, QUADRATIC_BENCHMARK_ID,
    persist_linear_prism_benchmark, persist_quadratic_prism_benchmark,
)
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
from app.v2.claim_integrity import is_authoritative_evidence

router=APIRouter(prefix="/api/v2/scientific",tags=["Backend V2 - Scientific Trust"])
class Inputs(BaseModel): inputs:dict[str,Any]
class BenchmarkRequest(BaseModel):
    inputs:dict[str,float]=Field(default_factory=dict)
    computed_result:float|None=None
    source_simulation_id:str|None=None
    benchmark_case_id:str|None=None
    benchmark_id:str|None=None
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
    metric_source:Literal["simulation_summary","benchmark_evidence"]="simulation_summary"
    benchmark_id:str|None=None
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
    if solver_id=="cfd_openfoam_laminar_internal_3d_v1":
        raise HTTPException(422,"CFD benchmark evidence is server-owned and cannot be created from an arbitrary user simulation")
    fem_solvers={"thermal_fem_3d_v1","structural_linear_elasticity_3d_v1","modal_fem_3d_v1"}
    if solver_id in fem_solvers:
        case_id=payload.benchmark_case_id or payload.benchmark_id
        if payload.benchmark_case_id and payload.benchmark_id and payload.benchmark_case_id!=payload.benchmark_id:
            raise HTTPException(422,"benchmark_case_id and benchmark_id disagree")
        if payload.source_simulation_id is None or case_id is None:
            raise HTTPException(422,"source_simulation_id and benchmark_case_id are required for CAD FEM benchmarks")
        if solver_id != "thermal_fem_3d_v1":
            raise HTTPException(422,"This CAD FEM solver has no server-bound authoritative analytical benchmark")
        repo=get_repository()
        try:
            source=resolve_simulation_source(payload.source_simulation_id,user["id"],require_completed_result=True,repository=repo)
            if source.solver_id!=solver_id:raise ValueError("Source simulation solver does not match benchmark solver")
            simulation_input=repo.get_simulation_input(source.simulation_id)
            if simulation_input is None:raise ValueError("Persisted FEM input is unavailable")
            mesh=_load_mesh(simulation_input.geometry.get("mesh_id",""),user["id"])
            kwargs={"repository":repo,"storage":get_storage(),"user_id":user["id"],"simulation_id":source.simulation_id,
                "mesh":mesh,"expected_parameters":payload.inputs}
            if case_id==LINEAR_BENCHMARK_ID: persisted=persist_linear_prism_benchmark(**kwargs)
            elif case_id==QUADRATIC_BENCHMARK_ID: persisted=persist_quadratic_prism_benchmark(**kwargs)
            else:raise ValueError("Unknown authoritative thermal FEM benchmark case")
            model=persisted["payload"]
            return {"benchmark_id":model["benchmark_id"],"solver_id":solver_id,"selected_metric":model["metric_name"],
                "computed_result":model["computed_value"],"reference_result":model["reference_value"],
                "absolute_error":model["absolute_error"],"relative_error":model["relative_error"],
                "declared_tolerance":model["tolerance"],"passed":model["passed"],
                "source_simulation_id":source.simulation_id,"created_from_real_computation":True,
                "authoritative_binding":model["case_binding"],"evidence_id":persisted["id"]}
        except SimulationSourceNotFoundError:raise HTTPException(404,"Source simulation not found")
        except (SimulationSourceError,LookupError,ValueError) as exc:raise HTTPException(422,str(exc))
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
    if solver_id=="cfd_openfoam_laminar_internal_3d_v1":raise HTTPException(422,"CFD analytical validation is server-owned")
    return reference_only(_item(solver_id),payload.inputs)
@router.post("/solvers/{solver_id}/convergence")
def execute_convergence(solver_id:str,payload:ConvergenceRequest,user:dict=Depends(get_current_user)):
    if solver_id=="cfd_openfoam_laminar_internal_3d_v1":raise HTTPException(422,"CFD refinement validation is server-owned")
    return {**convergence(_item(solver_id),payload.values,payload.configurations,payload.threshold),
            "authoritative":False,"evidence_id":None}
@router.post("/solvers/{solver_id}/refinement",status_code=201)
def execute_refinement(solver_id:str,payload:RefinementRequest,user:dict=Depends(get_current_user)):
    _item(solver_id)
    if solver_id=="cfd_openfoam_laminar_internal_3d_v1":raise HTTPException(422,"CFD refinement validation is server-owned")
    repo=get_repository()
    try:
        if payload.metric_source=="benchmark_evidence" and not payload.benchmark_id:
            raise ValueError("benchmark_id is required for benchmark-derived refinement")
        if payload.metric_source=="simulation_summary" and payload.benchmark_id:
            raise ValueError("benchmark_id requires metric_source=benchmark_evidence")
        first=resolve_simulation_source(payload.simulation_ids[0],user["id"],require_result=True,repository=repo)
        if first.solver_id!=solver_id:raise ValueError("Refinement source solver does not match route solver")
        record=create_refinement_evidence(
            user["id"],payload.simulation_ids,payload.selected_metric,
            payload.refinement_parameter,payload.threshold,
            benchmark_id=payload.benchmark_id if payload.metric_source=="benchmark_evidence" else None,
            repository=repo,
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
    if not is_authoritative_evidence(evidence,user["id"],row):
        raise HTTPException(422,"Scientific trust record provenance is invalid or legacy")
    return row
