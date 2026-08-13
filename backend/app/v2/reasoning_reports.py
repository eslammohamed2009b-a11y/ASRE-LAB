from __future__ import annotations
import csv,hashlib,io,json,tempfile,uuid
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
from dataclasses import asdict
from app.core.repository import get_repository
from app.core.storage import FileStorage,build_simulation_object_key,get_storage
from app.v2.execution import canonical_bytes,normalize
from app.v2.repository import EvidenceRepository
from app.v2.claim_integrity import classify_claim

LEVELS={"simple","engineering","research"}
STAGES={"workflow_planned","design_generation_planned","validity_check_started","validity_check_completed","job_queued","geometry_generated","solver_started","solver_progress","convergence_checked","scientific_trust_completed","design_classified","sensitivity_completed","pareto_completed","recommendation_produced","recommendation_approved","recommendation_rejected","reproduction_completed","report_generated","failure_occurred","cancellation_completed"}
def now():return datetime.now(timezone.utc).isoformat()
class ReasoningError(ValueError):pass
class OutputNotFound(LookupError):pass
def _safe_evidence(repo,user,ids):
    records=[]
    for id in ids:
        row=repo.get(id,user)
        if row:records.append(row)
    return records
def explain(stage,level,records,claim_assessments=None):
    if stage not in STAGES:raise ReasoningError("Unsupported reasoning stage")
    if level not in LEVELS:raise ReasoningError("Unsupported explanation level")
    if not records:return {"level":level,"summary":"There is not enough evidence to explain this result confidently.","facts":[],"evidence_ids":[],"next_action":"Collect or persist the required engineering evidence.","limitations":["Evidence is insufficient."]}
    facts=[];warnings=[];limitations=[];confidence=None
    claim_assessments=claim_assessments or {}
    for row in records:
        payload=row["payload"];confidence=payload.get("confidence",confidence)
        warnings.extend(payload.get("warnings",[]));limitations.extend(payload.get("limitations",[]))
        if row["record_type"]=="engineering_decision":
            rec=payload.get("recommendation",{});assessment=claim_assessments.get(row["id"])
            if assessment and assessment["classification"]=="finding":
                facts.append({"code":"RECOMMENDATION_STATUS","value":rec.get("statement"),"evidence_ids":assessment["evidence_ids"],"classification":"finding"})
            else:
                facts.append({"code":"INSUFFICIENT_EVIDENCE","value":"The recommendation is not an established research finding.","evidence_ids":[],"classification":"insufficient_evidence"})
        elif row["record_type"]=="scientific_trust":
            assessment=claim_assessments.get(row["id"])
            if assessment and assessment["classification"]=="finding":facts.append({"code":"SCIENTIFIC_TRUST","value":payload.get("overall_trust"),"evidence_id":row["id"],"classification":"finding"})
            else:facts.append({"code":"INSUFFICIENT_EVIDENCE","value":"Scientific trust provenance could not be resolved.","evidence_ids":[],"classification":"insufficient_evidence"})
        elif row["record_type"]=="run_manifest":
            facts.append({"code":"RUN_STATUS","value":payload.get("status"),"evidence_id":row["id"]})
        elif row["record_type"]=="job_attempt":
            facts.append({"code":"JOB_STAGE","value":payload.get("stage"),"evidence_id":row["id"]})
    summary={"simple":"Engineering evidence was processed. Review the result status and next action.",
      "engineering":"Persisted metrics, constraints, trade-offs, warnings, and confidence were evaluated deterministically.",
      "research":"Persisted solver, validity, benchmark, convergence, decision, and reproducibility evidence were evaluated using bounded models."}[level]
    return {"level":level,"summary":summary,"facts":facts,"evidence_ids":[x["id"] for x in records],
        "warnings":warnings,"limitations":limitations,"confidence":confidence,
        "next_action":"Review the linked evidence and choose the next controlled action.",
        "safety_statement":"This is an evidence summary, not hidden model chain-of-thought."}
class ReasoningService:
    def __init__(self,repo=None):self.repo=repo or EvidenceRepository(repository=get_repository())
    def create(self,user,experiment,stage,level,evidence_ids,context=None):
        records=_safe_evidence(self.repo,user,evidence_ids)
        assessments={}
        for row in records:
            if row["record_type"]=="engineering_decision":
                rec=row["payload"].get("recommendation",{})
                assessments[row["id"]]=classify_claim(self.repo,user,rec.get("statement","") or "",rec.get("evidence_ids",[]))
            elif row["record_type"]=="scientific_trust":
                assessments[row["id"]]=classify_claim(self.repo,user,"Scientific trust classification",[row["id"]])
        snapshot=explain(stage,level,records,assessments)
        payload={"event_id":str(uuid.uuid4()),"experiment_id":experiment,"stage":stage,"status":"completed",
            "summary_code":stage.upper(),"title":stage.replace("_"," ").title(),"reason_codes":[],
            "evidence_ids":snapshot["evidence_ids"],"metrics":normalize((context or {}).get("metrics",{})),
            "assumptions":normalize((context or {}).get("assumptions",[])),"warnings":snapshot.get("warnings",[]),
            "limitations":snapshot.get("limitations",[]),"confidence":snapshot.get("confidence"),
            "next_action":snapshot["next_action"],"created_at":now(),"explanation_version":"1.0","snapshot":snapshot}
        return self.repo.create(user,{"record_type":"reasoning_event","status":"completed","experiment_id":experiment,"simulation_id":(context or {}).get("job_id"),"parent_record_id":None,"payload":payload})
    def get(self,id,user,level=None):
        row=self.repo.get(id,user)
        if not row or row["record_type"]!="reasoning_event":raise OutputNotFound(id)
        if not level or level==row["payload"]["snapshot"]["level"]:return row
        records=_safe_evidence(self.repo,user,row["payload"]["evidence_ids"]);result=dict(row);result["payload"]=dict(row["payload"]);result["payload"]["snapshot"]=explain(row["payload"]["stage"],level,records);return result
def _pdf(text):
    safe=text.replace("\\","\\\\").replace("(","\\(").replace(")","\\)").replace("\n"," ")[:6000]
    stream=f"BT /F1 10 Tf 50 760 Td ({safe}) Tj ET".encode("latin-1","replace")
    objects=[b"<< /Type /Catalog /Pages 2 0 R >>",b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
      b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
      b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",b"<< /Length "+str(len(stream)).encode()+b" >>\nstream\n"+stream+b"\nendstream"]
    out=bytearray(b"%PDF-1.4\n");offset=[0]
    for i,obj in enumerate(objects,1):offset.append(len(out));out+=f"{i} 0 obj\n".encode()+obj+b"\nendobj\n"
    xref=len(out);out+=f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode()
    for x in offset[1:]:out+=f"{x:010d} 00000 n \n".encode()
    out+=f"trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode();return bytes(out)
class ReportService:
    def __init__(self,repo=None,storage=None,core_repo=None):
        self.core_repo=core_repo or get_repository();self.repo=repo or EvidenceRepository(repository=self.core_repo);self.storage=storage or get_storage()
    def create(self,user,experiment,title,evidence_ids):
        records=_safe_evidence(self.repo,user,evidence_ids)
        if len(records)!=len(evidence_ids):raise ReasoningError("All report evidence must exist and be owner-accessible")
        existing=next((x for x in self.repo.list(user,"research_report") if x["payload"].get("evidence_ids")==evidence_ids and x["payload"].get("title")==title),None)
        if existing:return existing
        sections={"title":{"text":title,"evidence_ids":[]},"experiment":{"id":experiment,"evidence_ids":[]}}
        experiment_record=self.core_repo.get_experiment(experiment)
        if experiment_record is not None and experiment_record.user_id != user:
            raise ReasoningError("Experiment is not owner-accessible")
        if experiment_record is not None:
            study=experiment_record.input_specification.get("study",{})
            designs=self.core_repo.list_design_models_for_experiment(experiment)
            simulations=self.core_repo.list_simulation_jobs_for_experiment(experiment)
            analyses=self.core_repo.list_analyses_for_experiment(experiment)
            alternatives=[{
                "design_id":design.id,"variation_index":design.variation_index,
                "geometry_family":design.geometry_family,"parameters":design.parameters,
                "units":design.units,"generation_status":design.generation_status,
            } for design in designs]
            simulation_evidence=[]
            for job in simulations:
                input_record=self.core_repo.get_simulation_input(job.id)
                result_record=self.core_repo.get_simulation_result(job.id)
                result_data=asdict(result_record) if result_record else "not available"
                if isinstance(result_data,dict):
                    result_data.pop("field_values",None)
                    result_data.pop("result_object_keys",None)
                simulation_evidence.append({
                    "simulation_id":job.id,"design_id":job.design_id,"solver_id":job.solver_id,
                    "status":job.status,"input":asdict(input_record) if input_record else "not available",
                    "result":result_data,
                })
            latest_analysis=asdict(analyses[-1]) if analyses else "not available"
            analysis_evidence=[] if not analyses else [item for item in self.repo.list(
                user,"scientific_analysis",experiment_id=experiment,
            ) if item["payload"].get("analysis_id")==analyses[-1].id]
            if analyses and not analysis_evidence:
                latest_analysis={"status":"insufficient_evidence","message":"Analysis provenance evidence is unavailable."}
            sections.update({
                "research_setup":{"data":{
                    "study_title":experiment_record.name,
                    "research_question":study.get("research_question","not available"),
                    "hypothesis":study.get("hypothesis") or "not available",
                    "independent_variables":study.get("independent_variables",[]),
                    "controlled_variables":study.get("controlled_variables",[]),
                    "output_variables":study.get("output_variables",[]),
                    "objectives":study.get("objectives",[]),
                },"evidence_ids":[]},
                "design_space":{"data":{
                    "definition":study.get("design_space") or experiment_record.input_specification.get("sweep_parameters") or "not available",
                    "alternatives":alternatives,
                },"evidence_ids":[item["design_id"] for item in alternatives]},
                "simulation_evidence":{"data":simulation_evidence,"evidence_ids":[item["simulation_id"] for item in simulation_evidence]},
                "analysis":{"data":latest_analysis,"evidence_ids":([analysis_evidence[-1]["id"]] if analysis_evidence else [])},
            })
        else:
            sections.update({
                "research_setup":{"data":"not available","evidence_ids":[]},
                "design_space":{"data":"not available","evidence_ids":[]},
                "simulation_evidence":{"data":"not available","evidence_ids":[]},
                "analysis":{"data":"not available","evidence_ids":[]},
            })
        for row in records:
            p=row["payload"];kind=row["record_type"]
            if kind=="scientific_trust":sections["scientific_trust"]={"data":p,"evidence_ids":[row["id"]]}
            elif kind=="run_manifest":sections["reproducibility"]={"data":p,"evidence_ids":[row["id"]]}
            elif kind=="engineering_decision":
                rec=p.get("recommendation",{})
                assessment=classify_claim(self.repo,user,rec.get("statement","") or "",rec.get("evidence_ids",[]))
                sections["decision_analysis"]={"data":p,"evidence_ids":[row["id"]],"claim_integrity":assessment}
            elif kind=="reasoning_event":sections["explanation"]={"data":p["snapshot"],"evidence_ids":[row["id"]]}
            elif kind=="job_attempt" and p.get("failure"):sections["failure"]={"data":p["failure"],"evidence_ids":[row["id"]]}
        report={"report_version":"2.0","generated_at":now(),"title":title,"experiment_id":experiment,"sections":sections,"evidence_ids":evidence_ids}
        json_bytes=canonical_bytes(report);rows=[]
        decision=sections.get("decision_analysis",{}).get("data",{})
        for item in decision.get("ranking",[]):rows.append({"table":"ranking","design_id":item["design_id"],"rank":item["rank"],"score":item["score"]})
        buffer=io.StringIO();writer=csv.DictWriter(buffer,fieldnames=["table","design_id","rank","score"]);writer.writeheader();writer.writerows(rows);csv_bytes=buffer.getvalue().encode()
        pdf_bytes=_pdf(title+"\n"+json.dumps(sections,sort_keys=True))
        report_id=str(uuid.uuid4());artifacts=[]
        for fmt,content,media in (("json",json_bytes,"application/json"),("csv",csv_bytes,"text/csv"),("pdf",pdf_bytes,"application/pdf")):
            key=build_simulation_object_key(user,experiment,report_id,f"research-report.{fmt}")
            with tempfile.NamedTemporaryFile(delete=False,suffix="."+fmt) as f:f.write(content);path=Path(f.name)
            try:self.storage.save_file(key,path)
            finally:path.unlink(missing_ok=True)
            artifacts.append({"format":fmt,"object_key":key,"checksum_sha256":hashlib.sha256(content).hexdigest(),"byte_size":len(content),"content_type":media,"private":True})
        payload={"report_id":report_id,"title":title,"experiment_id":experiment,"status":"completed","report":report,"artifacts":artifacts,"evidence_ids":evidence_ids,"report_checksum":hashlib.sha256(json_bytes).hexdigest(),"created_at":now()}
        return self.repo.create(user,{"record_type":"research_report","status":"completed","experiment_id":experiment,"simulation_id":None,"parent_record_id":None,"payload":payload})
    def get(self,id,user):
        row=self.repo.get(id,user)
        if not row or row["record_type"]!="research_report":raise OutputNotFound(id)
        return row
    def download(self,id,user,fmt):
        row=self.get(id,user);artifact=next((x for x in row["payload"]["artifacts"] if x["format"]==fmt),None)
        if not artifact:raise OutputNotFound(fmt)
        data=self.storage.open_bytes(artifact["object_key"])
        if hashlib.sha256(data).hexdigest()!=artifact["checksum_sha256"]:raise ReasoningError("Report artifact integrity failure")
        return data,artifact
