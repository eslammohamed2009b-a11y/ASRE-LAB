from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from fastapi import HTTPException
from pathlib import Path
import tempfile

from app.module1_design.nl_parser import parse_design_request
from app.module1_design.cadquery_engine import generate_model
from app.module1_design.multiprocessing_generator import generate_design_matrix
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
def generate_single(payload: PromptRequest):
    params = parse_design_request(payload.prompt)
    try:
        return GenerateSingleResponse(**generate_model(params))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/generate-matrix",
    summary="Generate design matrix in parallel",
    description="Builds multiple design variations using process-based parallel generation.",
)
def generate_matrix(request: DesignVariationRequest):
    """Generates the full Design Matrix in parallel (Module 1 -> Module 2 handoff)."""
    return generate_design_matrix(request)


@router.get(
    "/export/{design_id}",
    summary="Download generated STL",
    description="Returns STL file by design id if available in export storage.",
)
def export_stl(design_id: str):
    export_path = Path(tempfile.gettempdir()) / "asre_lab_exports" / f"{design_id}.stl"
    if not export_path.exists():
        raise HTTPException(status_code=404, detail="STL file not found for the given design_id")
    return FileResponse(str(export_path), filename=f"{design_id}.stl")
