from typing import Any
from fastapi import APIRouter,Depends,HTTPException,Response
from pydantic import BaseModel,Field
from app.core.auth import get_current_user
from app.v2.decisions import DecisionError,DecisionNotFound,DecisionService,analyse,feasibility,lhs,sensitivity,validate_constraints,validate_objectives
from app.v2.reasoning_reports import OutputNotFound,ReasoningError,ReasoningService,ReportService,public_record
router=APIRouter(prefix="/api/v2",tags=["Backend V2 - Decisions and Research"])
class DecisionRequest(BaseModel):
    experiment_id:str;designs:list[dict[str,Any]];objectives:list[dict[str,Any]];constraints:list[dict[str,Any]]=Field(default_factory=list);sensitivity_spec:dict[str,Any]|None=None
class DoeRequest(BaseModel):
    parameter_ranges:dict[str,tuple[float,float]];discrete_options:dict[str,list[Any]]=Field(default_factory=dict);sample_count:int;seed:int
class ActionRequest(BaseModel):action:str;comment:str|None=None
class ReasoningRequest(BaseModel):
    experiment_id:str;stage:str;level:str;evidence_ids:list[str];context:dict[str,Any]=Field(default_factory=dict)
class ReportRequest(BaseModel):experiment_id:str;title:str;evidence_ids:list[str]
class ItemsRequest(BaseModel):items:list[dict[str,Any]]
class FeasibilityRequest(BaseModel):design:dict[str,Any];constraints:list[dict[str,Any]]
class SensitivityRequest(BaseModel):designs:list[dict[str,Any]];parameters:list[str];target:str;method:str="both"
class AnalysisRequest(BaseModel):designs:list[dict[str,Any]];objectives:list[dict[str,Any]];constraints:list[dict[str,Any]]=Field(default_factory=list)
def call(fn,*args):
    try:return fn(*args)
    except (DecisionNotFound,OutputNotFound) as e:raise HTTPException(404,"Record not found") from e
    except (DecisionError,ReasoningError) as e:raise HTTPException(422,str(e)) from e
@router.post("/decisions/doe")
def doe(p:DoeRequest,user:dict=Depends(get_current_user)):return call(lhs,p.parameter_ranges,p.discrete_options,p.sample_count,p.seed)
@router.post("/decisions/objectives/validate")
def objective_validation(p:ItemsRequest,user:dict=Depends(get_current_user)):return {"valid":True,"objectives":call(validate_objectives,p.items)}
@router.post("/decisions/constraints/validate")
def constraint_validation(p:ItemsRequest,user:dict=Depends(get_current_user)):return {"valid":True,"constraints":call(validate_constraints,p.items)}
@router.post("/decisions/feasibility")
def evaluate_feasibility(p:FeasibilityRequest,user:dict=Depends(get_current_user)):return call(feasibility,p.design,call(validate_constraints,p.constraints))
@router.post("/decisions/sensitivity")
def evaluate_sensitivity(p:SensitivityRequest,user:dict=Depends(get_current_user)):return call(sensitivity,p.designs,p.parameters,p.target,p.method)
@router.post("/decisions/analyse")
def evaluate_decisions(p:AnalysisRequest,user:dict=Depends(get_current_user)):return call(analyse,p.designs,p.objectives,p.constraints)
@router.post("/decisions",status_code=201)
def decision(p:DecisionRequest,user:dict=Depends(get_current_user)):return call(DecisionService().create,user["id"],p.experiment_id,p.designs,p.objectives,p.constraints,p.sensitivity_spec)
@router.get("/decisions/{id}")
def get_decision(id:str,user:dict=Depends(get_current_user)):return call(DecisionService().get,id,user["id"])
@router.post("/decisions/{id}/actions")
def action(id:str,p:ActionRequest,user:dict=Depends(get_current_user)):return call(DecisionService().action,id,user["id"],p.action,p.comment)
@router.post("/reasoning",status_code=201)
def reasoning(p:ReasoningRequest,user:dict=Depends(get_current_user)):return call(ReasoningService().create,user["id"],p.experiment_id,p.stage,p.level,p.evidence_ids,p.context)
@router.get("/reasoning/{id}")
def get_reasoning(id:str,level:str|None=None,user:dict=Depends(get_current_user)):return call(ReasoningService().get,id,user["id"],level)
@router.post("/reports",status_code=201)
def report(p:ReportRequest,user:dict=Depends(get_current_user)):return call(ReportService().create,user["id"],p.experiment_id,p.title,p.evidence_ids)
@router.get("/reports/{id}")
def get_report(id:str,user:dict=Depends(get_current_user)):return call(ReportService().get,id,user["id"])
@router.get("/reports/{id}/artifacts")
def report_artifacts(id:str,user:dict=Depends(get_current_user)):return {"artifacts":public_record(call(ReportService().get,id,user["id"])["payload"]["artifacts"])}
@router.get("/reports/{id}/exports/{fmt}")
def export(id:str,fmt:str,user:dict=Depends(get_current_user)):
    data,meta=call(ReportService().download,id,user["id"],fmt)
    return Response(data,media_type=meta["content_type"],headers={"Content-Disposition":f'attachment; filename="research-report.{fmt}"'})
