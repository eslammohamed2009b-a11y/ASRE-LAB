from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_current_user
from app.module2_simulation.coupling import CouplingNotFoundError, ThermalStructuralCouplingRequest, get_coupling, run_thermal_structural_coupling

router = APIRouter(prefix="/api/couplings", tags=["Module 2 - Coupled Analysis"], dependencies=[Depends(get_current_user)])

@router.post("/thermal-structural")
def create_coupling(payload: ThermalStructuralCouplingRequest, current_user: dict = Depends(get_current_user)):
    try:
        return run_thermal_structural_coupling(payload, current_user["id"]).__dict__
    except CouplingNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Experiment or design not found") from exc

@router.get("/{coupling_id}")
def retrieve_coupling(coupling_id: str, current_user: dict = Depends(get_current_user)):
    try:
        return get_coupling(coupling_id, current_user["id"]).__dict__
    except CouplingNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Coupling not found") from exc
