from __future__ import annotations

from datetime import datetime, timezone
import pytest

from app.core.repository import AnalysisRecord, LocalSQLiteRepository
from app.core.storage import LocalFileStorage
from app.module3_analysis import feedback

pytestmark=pytest.mark.integration

def _setup(tmp_path):
    repo=LocalSQLiteRepository(tmp_path/"feedback.db"); exp=repo.create_experiment("user-a","feedback")
    design=repo.create_design_model(exp,"user-a","tower",{"base_length_m":10.0,"height_m":20.0,"wall_thickness_m":0.5,"geometry_type":"tower","material":"steel","slope_angle_deg":0.0},{"base_length_m":"m","height_m":"m"},0)
    now=datetime.now(timezone.utc).isoformat(); analysis=AnalysisRecord(id="analysis-a",experiment_id=exp,user_id="user-a",analysis_type="engineering_intelligence",status="completed",dataset_hash="a"*64,
        result={"recommendations":[{"statement":"review bounded height change","evidence":{"source_ids":[design]}}]},source_design_ids=[design],reproducibility_hash="b"*64,created_at=now,updated_at=now)
    repo.create_analysis(analysis); return repo,exp,design

def test_proposal_bounds_evidence_acceptance_lineage_owner_and_durability(tmp_path,monkeypatch):
    repo,exp,design=_setup(tmp_path)
    proposal=feedback.generate_proposal(feedback.ProposalRequest(analysis_id="analysis-a",source_design_id=design,parameter_bounds={"height_m":(10,30)}),"user-a",repo)
    assert proposal.status=="generated" and proposal.modifications[0]["to"]==21.0
    assert proposal.evidence and proposal.constraint_checks["height_m"]["original_within_bounds"]
    with pytest.raises(feedback.FeedbackStateError,match="accepted"):
        feedback.execute_proposal(proposal.id,"user-a",repo,LocalFileStorage(tmp_path/"objects"))
    feedback.transition_proposal(proposal.id,"user-a","accepted",repo)
    def fake_generate(*,repo,experiment_id,user_id,variation_index,params,**kwargs):
        repo.create_design_model(experiment_id,user_id,params.geometry_type.value,params.model_dump(mode="json"),{"length":"m"},variation_index,"completed")
    monkeypatch.setattr(feedback,"_generate_one_variant",fake_generate)
    iteration=feedback.execute_proposal(proposal.id,"user-a",repo,LocalFileStorage(tmp_path/"objects"))
    assert iteration.status=="completed" and iteration.parent_design_ids==[design] and len(iteration.child_design_ids)==1
    assert repo.get_design_proposal(proposal.id).status=="executed"
    restarted=LocalSQLiteRepository(tmp_path/"feedback.db")
    assert restarted.get_design_iteration(iteration.id).child_design_ids==iteration.child_design_ids
    with pytest.raises(feedback.FeedbackNotFoundError): feedback.get_proposal(proposal.id,"user-b",restarted)

def test_rejection_and_superseding_are_enforced(tmp_path):
    repo,_,design=_setup(tmp_path); request=feedback.ProposalRequest(analysis_id="analysis-a",source_design_id=design,parameter_bounds={"height_m":(10,30)})
    first=feedback.generate_proposal(request,"user-a",repo); second=feedback.generate_proposal(request,"user-a",repo)
    assert repo.get_design_proposal(first.id).status=="superseded"
    rejected=feedback.transition_proposal(second.id,"user-a","rejected",repo); assert rejected.status=="rejected"
    with pytest.raises(feedback.FeedbackStateError): feedback.transition_proposal(second.id,"user-a","accepted",repo)
