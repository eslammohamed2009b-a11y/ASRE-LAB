from __future__ import annotations
import math,uuid
from copy import deepcopy
from datetime import datetime,timezone
from typing import Any
import numpy as np
from scipy import stats
from app.core.config import settings
from app.v2.repository import EvidenceRepository

METRICS={
 "mass_kg":"kg","max_temperature_c":"degC","max_displacement_m":"m","max_stress_pa":"Pa",
 "natural_frequency_hz":"Hz","fundamental_resonance_hz":"Hz","max_electric_field_v_m":"V/m",
 "pressure_loss_pa":"Pa","maximum_velocity_m_s":"m/s","safety_margin":"ratio","cost_proxy":"index",
}
OPS={"less_than","less_than_or_equal","greater_than","greater_than_or_equal","equal_within_tolerance","inside_range","outside_range"}
CONFIDENCE={"invalid":0,"low":1,"moderate":2,"high":3}
def now():return datetime.now(timezone.utc).isoformat()
class DecisionError(ValueError):pass
class DecisionNotFound(LookupError):pass

def validate_objectives(items):
    if not items or not any(x.get("enabled",True) for x in items):raise DecisionError("At least one active objective is required")
    for x in items:
        if x.get("metric_code") not in METRICS:raise DecisionError("Unavailable objective metric")
        if x.get("direction") not in {"minimize","maximize"}:raise DecisionError("Invalid objective direction")
        if float(x.get("weight",0))<0:raise DecisionError("Objective weight must be non-negative")
        if x.get("unit")!=METRICS[x["metric_code"]]:raise DecisionError("Objective unit is incompatible")
    return items
def validate_constraints(items):
    for x in items:
        metric=x.get("metric_code")
        if metric not in METRICS and metric not in {"confidence","benchmark_passed","validity_status"}:raise DecisionError("Unavailable constraint metric")
        if x.get("operator") not in OPS:raise DecisionError("Invalid constraint operator")
        if metric in METRICS and x.get("unit")!=METRICS[metric]:raise DecisionError("Constraint unit is incompatible")
    return items
def _constraint(item,value):
    op=item["operator"];limit=item["limit_value"];tol=float(item.get("tolerance",0))
    if op=="less_than":passed=value<limit
    elif op=="less_than_or_equal":passed=value<=limit
    elif op=="greater_than":passed=value>limit
    elif op=="greater_than_or_equal":passed=value>=limit
    elif op=="equal_within_tolerance":passed=abs(value-limit)<=tol
    elif op=="inside_range":passed=limit[0]<=value<=limit[1]
    else:passed=value<limit[0] or value>limit[1]
    base=limit[0] if isinstance(limit,list) else limit
    return passed,float(value-base) if isinstance(value,(int,float)) else None
def feasibility(design,constraints):
    if design.get("validity_status")=="invalid":return {"classification":"invalid","reason_codes":["SCIENTIFIC_VALIDITY_INVALID"],"constraint_results":[]}
    if not design.get("evidence_ids"):return {"classification":"insufficient_evidence","reason_codes":["EVIDENCE_MISSING"],"constraint_results":[]}
    results=[]
    for item in constraints:
        if not item.get("enabled",True):continue
        metric=item["metric_code"]
        if metric=="confidence":
            observed=design.get("confidence","invalid");passed=CONFIDENCE.get(observed,0)>=CONFIDENCE.get(item.get("required_confidence","moderate"),2);margin=CONFIDENCE.get(observed,0)-CONFIDENCE.get(item.get("required_confidence","moderate"),2)
        else:
            observed=design.get("metrics",{}).get(metric)
            if observed is None:
                results.append({"constraint_id":item.get("constraint_id"),"passed":False,"observed_value":None,"reason_code":"METRIC_MISSING","evidence_references":design["evidence_ids"],"rejection_explanation":f"{metric} is missing."});continue
            passed,margin=_constraint(item,observed)
        results.append({"constraint_id":item.get("constraint_id"),"metric_code":metric,"passed":passed,"observed_value":observed,
            "limit":item.get("limit_value"),"margin":margin,"reason_code":"CONSTRAINT_PASSED" if passed else "CONSTRAINT_FAILED",
            "evidence_references":design["evidence_ids"],"rejection_explanation":None if passed else f"{metric} does not satisfy the declared limit."})
    failed=[x for x in results if not x["passed"]]
    return {"classification":"infeasible" if failed else "feasible","failed_constraints":[x.get("constraint_id") for x in failed],
        "constraint_results":results,"reason_codes":[x["reason_code"] for x in failed],"evidence_ids":design["evidence_ids"]}
def lhs(ranges,options,count,seed):
    if count<1 or count>settings.MAX_BATCH_VARIANTS:raise DecisionError("DOE sample count exceeds resource policy")
    rng=np.random.default_rng(seed);names=sorted(ranges);samples=[{} for _ in range(count)]
    for name in names:
        low,high=ranges[name];values=(rng.permutation(count)+rng.random(count))/count
        for i,value in enumerate(values):samples[i][name]=float(low+value*(high-low))
    for name,values in sorted(options.items()):
        for i in range(count):samples[i][name]=values[int(rng.integers(0,len(values)))]
    return {"sampling_method":"latin_hypercube","sample_count":count,"seed":seed,"parameter_ranges":ranges,
        "discrete_options":options,"samples":[{"sample_index":i,"parameters":x,"status":"generated","rejection_reasons":[]} for i,x in enumerate(samples)]}
def sensitivity(designs,parameters,target,method="both"):
    rows=[x for x in designs if target in x.get("metrics",{}) and all(p in x.get("parameters",{}) for p in parameters)]
    warnings=["Correlation indicates association, not proven physical causality."]
    if len(rows)<3:return {"target":target,"sample_count":len(rows),"influences":[],"warnings":warnings+["At least three complete samples are required."]}
    result=[]
    y=np.array([x["metrics"][target] for x in rows],float)
    for p in parameters:
        x=np.array([r["parameters"][p] for r in rows],float)
        if np.unique(x).size<2 or np.unique(y).size<2:
            result.append({"parameter":p,"status":"constant_variable","sample_count":len(rows)});continue
        pearson=float(stats.pearsonr(x,y).statistic);item={"parameter":p,"pearson":pearson,"signed_influence":pearson,"absolute_influence":abs(pearson),"sample_count":len(rows)}
        if method in {"spearman","both"}:item["spearman"]=float(stats.spearmanr(x,y).statistic)
        result.append(item)
    result.sort(key=lambda x:(-x.get("absolute_influence",0),x["parameter"]))
    return {"target":target,"sample_count":len(rows),"influences":result,"warnings":warnings}
def analyse(designs,objectives,constraints):
    validate_objectives(objectives);validate_constraints(constraints)
    evaluations={x["design_id"]:feasibility(x,constraints) for x in designs}
    feasible=[x for x in designs if evaluations[x["design_id"]]["classification"]=="feasible"]
    active=[x for x in objectives if x.get("enabled",True)];weights=np.array([x["weight"] for x in active],float)
    weights=weights/weights.sum() if weights.sum() else np.ones(len(active))/len(active)
    contributions={};scores={}
    for j,obj in enumerate(active):
        vals=[x["metrics"].get(obj["metric_code"]) for x in feasible];valid=[x for x in vals if x is not None]
        lo=min(valid) if valid else 0;hi=max(valid) if valid else 0
        for design,value in zip(feasible,vals):
            norm=.5 if hi==lo and value is not None else (0 if value is None else ((value-lo)/(hi-lo) if obj["direction"]=="maximize" else (hi-value)/(hi-lo)))
            part=float(norm*weights[j]);contributions.setdefault(design["design_id"],[]).append({"metric_code":obj["metric_code"],"raw_value":value,"normalized_value":norm,"direction":obj["direction"],"weight":float(weights[j]),"weighted_contribution":part})
            scores[design["design_id"]]=scores.get(design["design_id"],0)+part
    ranking=sorted([{"design_id":x["design_id"],"score":scores.get(x["design_id"],0),"contributions":contributions.get(x["design_id"],[]),"feasibility_state":"feasible","evidence_ids":x["evidence_ids"]} for x in feasible],key=lambda x:(-x["score"],x["design_id"]))
    for i,x in enumerate(ranking,1):x["rank"]=i
    pareto=[]
    for x in feasible:
        dominated=0
        for y in feasible:
            if x is y:continue
            better=[];strict=[]
            for obj in active:
                xv=x["metrics"].get(obj["metric_code"]);yv=y["metrics"].get(obj["metric_code"])
                if xv is None or yv is None:continue
                better.append(yv<=xv if obj["direction"]=="minimize" else yv>=xv);strict.append(yv<xv if obj["direction"]=="minimize" else yv>xv)
            if better and all(better) and any(strict):dominated+=1
        pareto.append({"design_id":x["design_id"],"dominance_count":dominated,"dominance_rank":1 if dominated==0 else 2,"pareto_member":dominated==0,"evidence_references":x["evidence_ids"]})
    return {"objectives":active,"constraints":constraints,"feasibility":evaluations,"ranking":ranking,"pareto":sorted(pareto,key=lambda x:x["design_id"]),
        "warnings":[] if len(feasible)>=2 else ["Too few feasible designs for strong trade-off evidence."]}

class DecisionService:
    def __init__(self,repo=None):self.repo=repo or EvidenceRepository()
    def create(self,user_id,experiment_id,designs,objectives,constraints,sensitivity_spec=None):
        result=analyse(designs,objectives,constraints);result["sensitivity"]=sensitivity(designs,**sensitivity_spec) if sensitivity_spec else None
        selected=result["ranking"][0] if result["ranking"] else None
        result["recommendation"]={"selected_design":selected["design_id"] if selected else None,"statement":"Best trade-off under the selected objectives, constraints, and weights." if selected else "No feasible design can be recommended.","ranking_score":selected["score"] if selected else None,"contribution_breakdown":selected["contributions"] if selected else [],"reason_codes":["FEASIBILITY_FIRST_WEIGHTED_RANKING"] if selected else ["NO_FEASIBLE_DESIGN"],"warnings":result["warnings"],"limitations":["Ranking depends on user-selected weights and available evidence."],"suggested_next_action":"Review and accept, reject, or request modification." if selected else "Correct failed constraints or collect evidence.","status":"proposed"}
        payload={"decision_id":str(uuid.uuid4()),"experiment_id":experiment_id,"status":"proposed","created_at":now(),**result}
        return self.repo.create(user_id,{"record_type":"engineering_decision","status":"proposed","experiment_id":experiment_id,"simulation_id":None,"parent_record_id":None,"payload":payload})
    def get(self,id,user):
        row=self.repo.get(id,user)
        if not row or row["record_type"]!="engineering_decision":raise DecisionNotFound(id)
        return row
    def action(self,id,user,action,comment=None):
        current=self.get(id,user);prior=[x for x in self.repo.list(user,"engineering_decision",current["id"]) if x["payload"].get("action")==action]
        if prior:return prior[0]
        if current["payload"]["status"]!="proposed":raise DecisionError("Recommendation is no longer actionable")
        status={"accept":"accepted","reject":"rejected","request_modification":"modification_requested"}.get(action)
        if not status:raise DecisionError("Invalid recommendation action")
        payload=deepcopy(current["payload"]);payload.update({"status":status,"action":action,"actor":user,"action_at":now(),"comment":comment})
        if action=="accept":payload["iteration_lineage"]={"source_design":payload["recommendation"]["selected_design"],"child_design":str(uuid.uuid4()),"source_decision":payload["decision_id"]}
        return self.repo.create(user,{"record_type":"engineering_decision","status":status,"experiment_id":current.get("experiment_id"),"simulation_id":None,"parent_record_id":current["id"],"payload":payload})
