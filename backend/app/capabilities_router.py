"""One public, registry-derived inventory for UI and future planners."""
from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.module1_design.capability_registry import list_design_capabilities
from app.module2_simulation.solver_registry import list_solvers
from app.module3_analysis.capability_registry import list_analysis_capabilities
from app.v2.scientific_trust import REGISTRY as TRUST_REGISTRY, metadata as trust_metadata

router = APIRouter(prefix="/api/capabilities", tags=["Capability Contracts"], dependencies=[Depends(get_current_user)])


@router.get("", summary="List authoritative design, simulation, analysis, and trust capabilities")
def capabilities() -> dict:
    return {
        "design": list_design_capabilities(),
        "simulation": [entry.model_dump() for entry in list_solvers()],
        "analysis": list_analysis_capabilities(),
        "scientific_trust": [trust_metadata(item) for item in TRUST_REGISTRY.list()],
    }
