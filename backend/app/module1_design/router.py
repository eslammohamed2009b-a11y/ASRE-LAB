import hashlib
import logging
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from fastapi import HTTPException

from app.module1_design.nl_parser import parse_design_request
from app.module1_design.cadquery_engine import generate_model
from app.module1_design.multiprocessing_generator import generate_design_matrix
from app.core.repository import get_repository
from app.core.auth import get_current_user
from app.module1_design.schemas import (
    DesignVariationRequest,
    GenerateSingleResponse,
    ParseResponse,
    PromptRequest,
)

logger = logging.getLogger(__name__)

EXPORT_DIR = (Path(tempfile.gettempdir()) / "asre_lab_exports").resolve()

router = APIRouter(
    prefix="/api/design",
    tags=["Module 1 - Design Accelerator"],
    dependencies=[Depends(get_current_user)],
)


def _is_valid_design_id(design_id: str) -> bool:
    """design_id is always a uuid4 minted by generate_model(); reject anything else
    up front (fail closed) rather than let a malformed/path-traversal-shaped value
    reach a lookup or filesystem path."""
    try:
        uuid.UUID(design_id)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _sha256_of_file(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_generated_file(
    repo,
    design_id: str,
    owner_id: str,
    experiment_id: str | None,
    file_path_str: str,
) -> None:
    file_path = Path(file_path_str)
    repo.record_design_file(
        design_id=design_id,
        owner_id=owner_id,
        experiment_id=experiment_id,
        file_format=file_path.suffix.lstrip(".") or "stl",
        storage_path=file_path_str,
        file_size_bytes=file_path.stat().st_size if file_path.exists() else None,
        checksum=_sha256_of_file(file_path),
    )


def _cleanup_generated_files(result: dict) -> None:
    """On a persistence failure, remove exported temp files so a failed
    generation never leaves an orphaned, unowned file discoverable on disk."""
    for key in ("stl_path", "step_path"):
        path_str = result.get(key)
        if not path_str:
            continue
        try:
            Path(path_str).unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to clean up temp export file after persistence error", exc_info=True)


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

    repo = get_repository()
    try:
        experiment_id = repo.create_experiment(
            owner_id=current_user["id"],
            title=f"generate-single: {params.geometry_type}",
        )
        _record_generated_file(repo, result["design_id"], current_user["id"], experiment_id, result["stl_path"])
    except Exception:
        logger.error("Failed to persist ownership for generated design %s", result["design_id"], exc_info=True)
        _cleanup_generated_files(result)
        raise HTTPException(
            status_code=500,
            detail="Design was generated but could not be recorded; it has not been saved. Please retry.",
        )
    return GenerateSingleResponse(**result)


@router.post(
    "/generate-matrix",
    summary="Generate design matrix in parallel",
    description="Builds multiple design variations using process-based parallel generation.",
)
def generate_matrix(request: DesignVariationRequest, current_user: dict = Depends(get_current_user)):
    """Generates the full Design Matrix in parallel (Module 1 -> Module 2 handoff)."""
    results = generate_design_matrix(request)
    repo = get_repository()
    try:
        experiment_id = repo.create_experiment(
            owner_id=current_user["id"],
            title="generate-matrix batch",
        )
        for design in results:
            if "design_id" in design and design.get("stl_path"):
                _record_generated_file(repo, design["design_id"], current_user["id"], experiment_id, design["stl_path"])
    except Exception:
        logger.error("Failed to persist ownership for a design matrix batch", exc_info=True)
        for design in results:
            _cleanup_generated_files(design)
        raise HTTPException(
            status_code=500,
            detail="Design matrix was generated but could not be recorded; it has not been saved. Please retry.",
        )
    return results


@router.get(
    "/export/{design_id}",
    summary="Download generated STL",
    description="Returns STL file by design id if available in export storage.",
)
def export_stl(design_id: str, current_user: dict = Depends(get_current_user)):
    if not _is_valid_design_id(design_id):
        # Malformed ids are rejected the same way unknown ids are (404, not 400)
        # so a caller cannot distinguish "not a real id" from "not yours" -
        # avoids leaking a format oracle that would aid enumeration.
        raise HTTPException(status_code=404, detail="STL file not found for the given design_id")

    repo = get_repository()
    record = repo.get_design_file(design_id)
    if record is None or record.owner_id != current_user["id"]:
        # Fail closed: an unknown owner (e.g. generated before this process started,
        # on a different replica, or belonging to someone else) is treated the same -
        # never silently serve a file without a matching, recorded owner.
        raise HTTPException(status_code=404, detail="STL file not found for the given design_id")

    # Never trust the stored path directly for the response - recompute the
    # expected path from the validated design_id and confirm it resolves
    # inside EXPORT_DIR (defense against path traversal / a corrupted record).
    export_path = (EXPORT_DIR / f"{design_id}.stl").resolve()
    if export_path.parent != EXPORT_DIR or not export_path.exists():
        raise HTTPException(status_code=404, detail="STL file not found for the given design_id")

    return FileResponse(
        str(export_path),
        filename=f"{design_id}.stl",
        media_type="model/stl",
    )

