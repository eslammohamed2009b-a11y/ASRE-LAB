import hashlib
import pytest
from app.core.storage import LocalFileStorage
from app.v2.decisions import DecisionError,DecisionService,analyse,feasibility,lhs,sensitivity,validate_constraints,validate_objectives
from app.v2.reasoning_reports import ReasoningService,ReportService,explain
from app.v2.repository import EvidenceRepository

def objectives():
 return [{"objective_id":"o1","metric_code":"max_temperature_c","direction":"minimize","weight":.7,"unit":"degC","enabled":True},
 {"objective_id":"o2","metric_code":"safety_margin","direction":"maximize","weight":.3,"unit":"ratio","enabled":True}]
def constraints():
 return [{"constraint_id":"c1","metric_code":"max_temperature_c","operator":"less_than_or_equal","limit_value":100,"unit":"degC","enabled":True},
 {"constraint_id":"c2","metric_code":"confidence","operator":"greater_than_or_equal","limit_value":2,"required_confidence":"moderate","enabled":True}]
def designs():
 return [{"design_id":"a","metrics":{"max_temperature_c":80,"safety_margin":2},"parameters":{"width":1},"confidence":"high","validity_status":"valid","evidence_ids":["e1"]},
 {"design_id":"b","metrics":{"max_temperature_c":120,"safety_margin":3},"parameters":{"width":2},"confidence":"moderate","validity_status":"valid","evidence_ids":["e2"]},
 {"design_id":"c","metrics":{"max_temperature_c":90,"safety_margin":1},"parameters":{"width":3},"confidence":"high","validity_status":"valid","evidence_ids":["e3"]}]
@pytest.fixture
def services(tmp_path):
 repo=EvidenceRepository(str(tmp_path/"db.sqlite"));storage=LocalFileStorage(tmp_path/"objects")
 return repo,DecisionService(repo),ReasoningService(repo),ReportService(repo,storage)
def test_objective_constraint_validation_and_units():
 assert validate_objectives(objectives())
 with pytest.raises(DecisionError):validate_objectives([{**objectives()[0],"metric_code":"invented"}])
 with pytest.raises(DecisionError):validate_objectives([{**objectives()[0],"weight":-1}])
 with pytest.raises(DecisionError):validate_constraints([{**constraints()[0],"operator":"bad"}])
 with pytest.raises(DecisionError):validate_constraints([{**constraints()[0],"unit":"K"}])
def test_constraint_feasibility_states_and_margin():
 good=feasibility(designs()[0],constraints());bad=feasibility(designs()[1],constraints())
 assert good["classification"]=="feasible"
 assert bad["classification"]=="infeasible" and bad["constraint_results"][0]["margin"]==20
 assert feasibility({**designs()[0],"validity_status":"invalid"},constraints())["classification"]=="invalid"
 assert feasibility({**designs()[0],"evidence_ids":[]},constraints())["classification"]=="insufficient_evidence"
def test_seeded_latin_hypercube_bounds_and_limit():
 a=lhs({"x":(0,1)},{"material":["steel","air"]},5,42);b=lhs({"x":(0,1)},{"material":["steel","air"]},5,42)
 assert a==b and all(0<=x["parameters"]["x"]<=1 for x in a["samples"])
 with pytest.raises(DecisionError):lhs({"x":(0,1)},{},26,1)
def test_sensitivity_pearson_spearman_constant_and_warning():
 result=sensitivity(designs(),["width"],"max_temperature_c")
 assert result["influences"][0]["pearson"]==pytest.approx(.2401922307)
 assert "spearman" in result["influences"][0] and "not proven physical causality" in result["warnings"][0]
 constant=sensitivity([{**x,"parameters":{"width":1}} for x in designs()],["width"],"max_temperature_c")
 assert constant["influences"][0]["status"]=="constant_variable"
 assert sensitivity(designs()[:2],["width"],"max_temperature_c")["influences"]==[]
def test_pareto_ranking_feasibility_first_contributions_and_stability():
 result=analyse(designs(),objectives(),constraints())
 assert [x["design_id"] for x in result["ranking"]]==["a","c"]
 assert result["ranking"][0]["score"]==pytest.approx(sum(x["weighted_contribution"] for x in result["ranking"][0]["contributions"]))
 assert all(x["design_id"]!="b" for x in result["pareto"] if x["pareto_member"])
 assert analyse(designs(),objectives(),constraints())==result
def test_recommendation_actions_idempotency_lineage_and_ownership(services):
 repo,service,_,_=services
 record=service.create("owner","exp",designs(),objectives(),constraints(),{"parameters":["width"],"target":"max_temperature_c"})
 assert record["payload"]["recommendation"]["selected_design"]=="a"
 accepted=service.action(record["id"],"owner","accept","approved");again=service.action(record["id"],"owner","accept","approved")
 assert accepted["id"]==again["id"] and accepted["payload"]["iteration_lineage"]["child_design"]
 with pytest.raises(Exception):service.get(record["id"],"other")
 rejected=service.create("owner","exp2",designs(),objectives(),constraints());assert service.action(rejected["id"],"owner","reject")["status"]=="rejected"
 modified=service.create("owner","exp3",designs(),objectives(),constraints());assert service.action(modified["id"],"owner","request_modification")["status"]=="modification_requested"
def test_reasoning_levels_missing_evidence_snapshots_restart_and_owner(services):
 repo,decision,reasoning,_=services
 record=decision.create("owner","exp",designs(),objectives(),constraints())
 for level in ("simple","engineering","research"):
  event=reasoning.create("owner","exp","recommendation_produced",level,[record["id"]])
  assert event["payload"]["snapshot"]["level"]==level and event["payload"]["snapshot"]["evidence_ids"]==[record["id"]]
 missing=reasoning.create("owner","exp","workflow_planned","simple",["missing"])
 assert "not enough evidence" in missing["payload"]["snapshot"]["summary"]
 reloaded=ReasoningService(EvidenceRepository(repo.path));assert reloaded.get(event["id"],"owner")["id"]==event["id"]
 with pytest.raises(Exception):reloaded.get(event["id"],"other")
 assert "chain-of-thought" in event["payload"]["snapshot"]["safety_statement"]
def test_report_sections_exports_integrity_idempotency_restart_and_owner(services):
 repo,decision,reasoning,reports=services
 d=decision.create("owner","exp",designs(),objectives(),constraints())
 r=reasoning.create("owner","exp","recommendation_produced","research",[d["id"]])
 report=reports.create("owner","exp","Bounded Study",[d["id"],r["id"]]);again=reports.create("owner","exp","Bounded Study",[d["id"],r["id"]])
 assert report["id"]==again["id"] and "decision_analysis" in report["payload"]["report"]["sections"]
 for fmt in ("json","csv","pdf"):
  data,meta=reports.download(report["id"],"owner",fmt);assert hashlib.sha256(data).hexdigest()==meta["checksum_sha256"]
 assert reports.download(report["id"],"owner","pdf")[0].startswith(b"%PDF")
 reloaded=ReportService(EvidenceRepository(repo.path),reports.storage);assert reloaded.get(report["id"],"owner")["id"]==report["id"]
 with pytest.raises(Exception):reloaded.get(report["id"],"other")
 content=reports.download(report["id"],"owner","json")[0]
 assert b"C:\\Users\\" not in content and b"X-Amz-Signature" not in content
