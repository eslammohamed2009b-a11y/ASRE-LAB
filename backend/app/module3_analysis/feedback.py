"""Persisted, reviewable engineering-intelligence to design-iteration workflow."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel

from app.core.repository import DesignIterationRecord, DesignProposalRecord, PersistenceRepository, get_repository
from app.core.storage import get_storage
from app.module1_design.schemas import DesignParameters
from app.module1_design.tasks import _generate_one_variant

PROPOSAL_STATES={"generated","accepted","rejected","superseded","executed","failed"}

class ProposalRequest(BaseModel):
    analysis_id: str
    source_design_id: str
    parameter_bounds: dict[str, tuple[float,float]]

class ProposalResponse(BaseModel):
    id: str
    experiment_id: str
    analysis_id: str
    user_id: str
    status: Literal["generated","accepted","rejected","superseded","executed","failed"]
    modifications: list[dict[str, Any]]
    evidence: list[Any]
    source_design_ids: list[str]
    expected_tradeoffs: list[str]
    confidence_limitations: list[str]
    constraint_checks: dict[str, Any]
    created_at: str
    updated_at: str

class IterationResponse(BaseModel):
    id: str
    experiment_id: str
    proposal_id: str
    user_id: str
    parent_design_ids: list[str]
    child_design_ids: list[str]
    status: Literal["planned","completed","failed"]
    created_at: str
    updated_at: str

class FeedbackNotFoundError(LookupError): pass
class FeedbackStateError(ValueError): pass

def _owned(repo, proposal_id, user_id):
    record=repo.get_design_proposal(proposal_id)
    if record is None or record.user_id != user_id: raise FeedbackNotFoundError("Proposal not found")
    return record

def generate_proposal(request: ProposalRequest,user_id: str,repository: PersistenceRepository|None=None)->DesignProposalRecord:
    repo=repository or get_repository(); analysis=repo.get_analysis(request.analysis_id)
    if analysis is None or analysis.user_id != user_id: raise FeedbackNotFoundError("Analysis not found")
    designs={d.id:d for d in repo.list_design_models_for_experiment(analysis.experiment_id)}
    design=designs.get(request.source_design_id)
    if design is None or design.user_id != user_id: raise FeedbackNotFoundError("Design not found")
    modifications=[]; checks={}
    for name,bounds in sorted(request.parameter_bounds.items()):
        lower,upper=map(float,bounds); current=design.parameters.get(name)
        valid=lower<upper and isinstance(current,(int,float)) and not isinstance(current,bool) and lower<=current<=upper
        checks[name]={"lower":lower,"upper":upper,"original":current,"original_within_bounds":valid}
        if valid:
            proposed=min(upper,max(lower,float(current)+0.05*(upper-lower)))
            if proposed != current:
                modifications.append({"parameter":name,"from":float(current),"to":proposed,"unit":design.units.get(name),
                    "method":"bounded five-percent-range review step"})
    if not modifications: raise FeedbackStateError("No bounded numeric parameter can be modified")
    evidence=analysis.result.get("recommendations",[])
    now=datetime.now(timezone.utc).isoformat(); record=DesignProposalRecord(id=str(uuid.uuid4()),experiment_id=analysis.experiment_id,
        analysis_id=analysis.id,user_id=user_id,status="generated",modifications=modifications,evidence=evidence,
        source_design_ids=[design.id],expected_tradeoffs=["Changing one or more geometry parameters may improve selected objectives while degrading others; re-simulation is required."],
        confidence_limitations=["This is a bounded review proposal, not a guaranteed prediction.","Association is not causation; standardized regression is not global sensitivity."],
        constraint_checks=checks,created_at=now,updated_at=now)
    for prior in repo.list_design_proposals(analysis.experiment_id):
        if prior.status=="generated": repo.update_design_proposal_status(prior.id,"superseded")
    repo.create_design_proposal(record); return record

def list_proposals(experiment_id,user_id,repository=None):
    repo=repository or get_repository(); exp=repo.get_experiment(experiment_id)
    if exp is None or exp.user_id!=user_id: raise FeedbackNotFoundError("Experiment not found")
    return repo.list_design_proposals(experiment_id)

def get_proposal(proposal_id,user_id,repository=None): return _owned(repository or get_repository(),proposal_id,user_id)

def transition_proposal(proposal_id,user_id,target,repository=None):
    repo=repository or get_repository(); record=_owned(repo,proposal_id,user_id)
    allowed={"generated":{"accepted","rejected"},"accepted":{"rejected"}}
    if target not in allowed.get(record.status,set()): raise FeedbackStateError(f"Cannot transition {record.status} to {target}")
    repo.update_design_proposal_status(proposal_id,target); return _owned(repo,proposal_id,user_id)

def execute_proposal(proposal_id,user_id,repository=None,storage=None):
    repo,storage=repository or get_repository(),storage or get_storage(); proposal=_owned(repo,proposal_id,user_id)
    if proposal.status!="accepted": raise FeedbackStateError("Proposal must be explicitly accepted before execution")
    designs={d.id:d for d in repo.list_design_models_for_experiment(proposal.experiment_id)}
    parent=designs.get(proposal.source_design_ids[0]); params=dict(parent.parameters)
    for change in proposal.modifications: params[change["parameter"]]=change["to"]
    iteration_id=str(uuid.uuid4()); now=datetime.now(timezone.utc).isoformat()
    try:
        before={d.id for d in designs.values()}; variation=max((d.variation_index for d in designs.values()),default=-1)+1
        _generate_one_variant(repo=repo,storage=storage,experiment_id=proposal.experiment_id,user_id=user_id,
            variation_index=variation,params=DesignParameters(**params))
        children=[d.id for d in repo.list_design_models_for_experiment(proposal.experiment_id) if d.id not in before]
        status="completed"; repo.update_design_proposal_status(proposal.id,"executed")
    except Exception:
        children=[]; status="failed"; repo.update_design_proposal_status(proposal.id,"failed")
    record=DesignIterationRecord(id=iteration_id,experiment_id=proposal.experiment_id,proposal_id=proposal.id,user_id=user_id,
        parent_design_ids=proposal.source_design_ids,child_design_ids=children,status=status,created_at=now,updated_at=now)
    repo.create_design_iteration(record); return record

def list_iterations(experiment_id,user_id,repository=None):
    repo=repository or get_repository(); exp=repo.get_experiment(experiment_id)
    if exp is None or exp.user_id!=user_id: raise FeedbackNotFoundError("Experiment not found")
    return repo.list_design_iterations(experiment_id)
