from fastapi import APIRouter,Depends,HTTPException
from app.core.auth import get_current_user
from app.module3_analysis.feedback import *

router=APIRouter(prefix="/api/design-feedback",tags=["Module 3 - Reviewable Design Feedback"],dependencies=[Depends(get_current_user)])

def _call(fn,*args):
    try:return fn(*args)
    except FeedbackNotFoundError as exc: raise HTTPException(404,"Record not found") from exc
    except FeedbackStateError as exc: raise HTTPException(409,str(exc)) from exc

@router.post("/proposals")
def create(payload:ProposalRequest,current_user:dict=Depends(get_current_user)): return _call(generate_proposal,payload,current_user["id"]).__dict__
@router.get("/experiments/{experiment_id}/proposals")
def listing(experiment_id:str,current_user:dict=Depends(get_current_user)): return [x.__dict__ for x in _call(list_proposals,experiment_id,current_user["id"])]
@router.get("/proposals/{proposal_id}")
def retrieve(proposal_id:str,current_user:dict=Depends(get_current_user)): return _call(get_proposal,proposal_id,current_user["id"]).__dict__
@router.post("/proposals/{proposal_id}/accept")
def accept(proposal_id:str,current_user:dict=Depends(get_current_user)): return _call(transition_proposal,proposal_id,current_user["id"],"accepted").__dict__
@router.post("/proposals/{proposal_id}/reject")
def reject(proposal_id:str,current_user:dict=Depends(get_current_user)): return _call(transition_proposal,proposal_id,current_user["id"],"rejected").__dict__
@router.post("/proposals/{proposal_id}/execute")
def execute(proposal_id:str,current_user:dict=Depends(get_current_user)): return _call(execute_proposal,proposal_id,current_user["id"]).__dict__
@router.get("/experiments/{experiment_id}/iterations")
def iterations(experiment_id:str,current_user:dict=Depends(get_current_user)): return [x.__dict__ for x in _call(list_iterations,experiment_id,current_user["id"])]
