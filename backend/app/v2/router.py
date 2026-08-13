from fastapi import APIRouter,Depends,HTTPException
from app.core.auth import get_current_user
from app.v2.repository import EvidenceRepository
from app.v2.schemas import EvidenceCreate,EvidenceResponse
from app.v2.reasoning_reports import public_record
router=APIRouter(prefix="/api/v2/evidence",tags=["Backend V2"])
@router.post("",response_model=EvidenceResponse,status_code=201)
def create(payload:EvidenceCreate,user:dict=Depends(get_current_user)):
    if payload.record_type == "scientific_trust":
        raise HTTPException(422,"Scientific trust can only be derived from persisted evidence through the scientific trust endpoint")
    result=public_record(EvidenceRepository().create(user["id"],payload.model_dump()))
    result.pop("user_id",None)
    return result
@router.get("/{record_id}",response_model=EvidenceResponse)
def get(record_id:str,user:dict=Depends(get_current_user)):
    row=EvidenceRepository().get(record_id,user["id"])
    if row is None:raise HTTPException(404,"Evidence record not found")
    result=public_record(row);result.pop("user_id",None);return result
