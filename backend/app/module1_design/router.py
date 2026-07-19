from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from fastapi import HTTPException
from pathlib import Path
import tempfile

from app.module1_design.nl_parser import parse_design_request
from app.module1_design.cadquery_engine import generate_model
from app.module1_design.multiprocessing_generator import generate_design_matrix
from app.module1_design import ownership_store
from app.core.auth import get_current_user
from app.module1_design.schemas import (
    DesignVariationRequest,
    GenerateSingleResponse,
    ParseResponse,
    PromptRequest,
)

router = APIRouter(
    prefix="/api/design",
    tags=["Module 1 - Design Accelerator"],
    dependencies=[Depends(get_current_user)],
)


@router.post(
    "/parse",
    response_model=ParseResponse,
    summary="Parse natural language into design parameters",
    description="Converts user prompt into validated engineering parameters with defaults.",
)
def parse_prompt(payload: PromptRequest):
    """Natural language -> DesignParameters (Input Protocol)."""
    params = parse_design_request(payload.prompt)
    return ParseResponse(params=params)


@router.post(
    "/generate-single",
    response_model=GenerateSingleResponse,
    summary="Generate a single parametric model",
    description="Parses the prompt and exports STL/STEP files for one design.",
)
def generate_single(payload: PromptRequest, current_user: dict = Depends(get_current_user)):
    params = parse_design_request(payload.prompt)
    try:
        result = generate_model(params)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ownership_store.record_owner(result["design_id"], current_user["id"])
    return GenerateSingleResponse(**result)


@router.post(
    "/generate-matrix",
    summary="Generate design matrix in parallel",
    description="Builds multiple design variations using process-based parallel generation.",
)
def generate_matrix(request: DesignVariationRequest, current_user: dict = Depends(get_current_user)):
    """Generates the full Design Matrix in parallel (Module 1 -> Module 2 handoff)."""
    results = generate_design_matrix(request)
    for design in results:
        if "design_id" in design:
            ownership_store.record_owner(design["design_id"], current_user["id"])
    return results


@router.get(
    "/export/{design_id}",
    summary="Download generated STL",
    description="Returns STL file by design id if available in export storage.",
)
def export_stl(design_id: str, current_user: dict = Depends(get_current_user)):
    owner_id = ownership_store.get_owner(design_id)
    if owner_id is None or owner_id != current_user["id"]:
        # Fail closed: an unknown owner (e.g. generated before this process started,
        # or on a different replica) is treated the same as someone else's design -
        # never silently serve a file without a matching, recorded owner.
        raise HTTPException(status_code=404, detail="STL file not found for the given design_id")

    export_path = Path(tempfile.gettempdir()) / "asre_lab_exports" / f"{design_id}.stl"
    if not export_path.exists():
        raise HTTPException(status_code=404, detail="STL file not found for the given design_id")
    return FileResponse(str(export_path), filename=f"{design_id}.stl")
