import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Header
from fastapi import HTTPException

from app.module1_design.nl_parser import parse_design_request
from app.module1_design.capability_registry import GeometryClassificationError
from app.module1_design.cadquery_engine import generate_model
from app.module1_design.multiprocessing_generator import generate_design_matrix
from app.module1_design.design_space import build_design_space
from app.module1_design.cad_v2_compiler import (
    CADCompilationError,
    CAD_V2_CAPABILITY_CONTRACT,
    compile_design,
    design_hash as v2_design_hash,
    export_compiled_design,
    normalized_parameter_state,
)
from app.module1_design.cad_v2_schemas import (
    DesignV2CompileResponse,
    DesignV2ValidationResponse,
    EngineeringDesignDocumentV2,
)
from app.module1_design.cad_v2_design_space import (
    V2DesignSpacePreview,
    V2DesignSpaceRequest,
    build_design_variants,
)
from app.module1_design.cad_v2_plan import DesignIntentPlan, DesignPlanAssessment, assess_design_plan
from app.core.config import settings
from app.core.repository import get_repository
from app.core.storage import build_object_key, get_storage
from app.core.auth import get_current_user
from app.module1_design.schemas import (
    BatchGenerateRequest,
    BatchGenerateResponse,
    DesignSpacePreviewResponse,
    DesignSpaceRequest,
    DesignVariationRequest,
    GenerateSingleResponse,
    ParseResponse,
    PromptRequest,
)

logger = logging.getLogger(__name__)

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


def _upload_generated_files(
    repo,
    storage,
    design_id: str,
    owner_id: str,
    experiment_id: str,
    result: dict,
) -> None:
    """Upload a freshly-generated design's STL/STEP scratch files through the
    FileStorage abstraction and persist their durable ownership records.
    Deletes the scratch temp files once they are durably stored (or on
    failure, so a failed upload never leaves an orphaned temp file)."""
    try:
        for key in ("stl_path", "step_path"):
            scratch_path = Path(result[key])
            object_key = build_object_key(owner_id, experiment_id, design_id, scratch_path.name)
            checksum = storage.calculate_checksum(scratch_path)
            size_bytes = scratch_path.stat().st_size
            storage.save_file(object_key, scratch_path)
            file_id = design_id if key == "stl_path" else str(uuid.uuid4())
            repo.record_design_file(
                design_id=file_id,
                owner_id=owner_id,
                experiment_id=experiment_id,
                file_format=scratch_path.suffix.lstrip(".") or "bin",
                storage_provider="supabase" if type(storage).__name__ == "SupabaseStorage" else "local",
                object_key=object_key,
                file_size_bytes=size_bytes,
                checksum_sha256=checksum,
                media_type="model/stl" if key == "stl_path" else "model/step",
            )
    finally:
        _cleanup_generated_files(result)


def _cleanup_generated_files(result: dict) -> None:
    """On a persistence/upload failure, remove exported scratch temp files so
    a failed generation never leaves an orphaned, unowned file discoverable
    on disk."""
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
    try:
        params = parse_design_request(payload.prompt)
    except GeometryClassificationError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc
    return ParseResponse(params=params)


@router.get(
    "/v2/capabilities",
    summary="Get the truthful general CAD V2 capability contract",
)
def v2_capabilities() -> dict:
    return CAD_V2_CAPABILITY_CONTRACT


@router.post(
    "/v2/validate",
    response_model=DesignV2ValidationResponse,
    summary="Validate and normalize a typed CAD V2 design document",
)
def validate_v2_document(payload: EngineeringDesignDocumentV2) -> DesignV2ValidationResponse:
    return DesignV2ValidationResponse(
        valid=True,
        design_hash=v2_design_hash(payload),
        feature_order=payload.deterministic_feature_order(),
        normalized_parameters=normalized_parameter_state(payload),
    )


@router.post(
    "/v2/design-space/preview",
    response_model=V2DesignSpacePreview,
    summary="Preview a deterministic generic V2 design-variable space",
)
def preview_v2_design_space(payload: V2DesignSpaceRequest) -> V2DesignSpacePreview:
    try:
        variants = build_design_variants(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return V2DesignSpacePreview(
        variant_count=len(variants),
        chunk_size=payload.chunk_size,
        variant_ids=[item.variant_id for item in variants],
        parameter_values=[item.parameter_values for item in variants],
    )


@router.post(
    "/v2/plan/validate",
    response_model=DesignPlanAssessment,
    summary="Validate a non-authoritative typed CAD design-intent plan",
)
def validate_v2_design_plan(payload: DesignIntentPlan) -> DesignPlanAssessment:
    return assess_design_plan(payload)


@router.post(
    "/v2/compile",
    response_model=DesignV2CompileResponse,
    summary="Compile, validate, and privately store a typed CAD V2 design",
)
def compile_v2_document(
    payload: EngineeringDesignDocumentV2,
    current_user: dict = Depends(get_current_user),
) -> DesignV2CompileResponse:
    try:
        compiled = compile_design(payload)
        artifacts = export_compiled_design(compiled)
    except CADCompilationError as exc:
        detail = {
            "code": exc.code,
            "message": str(exc),
            "feature_id": exc.feature_id,
            "body_id": exc.body_id,
            "suggested_correction": exc.suggested_correction,
        }
        if exc.validation is not None:
            detail["validation"] = exc.validation.model_dump(mode="json")
        raise HTTPException(status_code=422, detail=detail) from exc

    repo = get_repository()
    storage = get_storage()
    experiment_id: str | None = None
    design_model_id: str | None = None
    saved_keys: list[str] = []
    try:
        experiment_id = repo.create_experiment(
            user_id=current_user["id"],
            name=f"CAD V2: {payload.document_id}",
            input_specification={
                "schema_version": payload.schema_version,
                "design_document": payload.model_dump(mode="json"),
                "design_hash": compiled.design_hash,
            },
        )
        design_model_id = repo.create_design_model(
            experiment_id=experiment_id,
            user_id=current_user["id"],
            geometry_family="engineering_design_v2",
            parameters={
                "schema_version": payload.schema_version,
                "normalized_parameters": compiled.normalized_parameters,
                "design_hash": compiled.design_hash,
                "geometry_fingerprint": compiled.geometry_fingerprint,
                "feature_order": compiled.feature_order,
                "semantic_regions": compiled.semantic_regions,
                "sketch_solve_results": [
                    item.model_dump(mode="json") for item in compiled.sketch_solve_results
                ],
                "assembly_validation": (
                    compiled.assembly.validation.model_dump(mode="json") if compiled.assembly else None
                ),
                "feature_hashes": compiled.feature_hashes,
                "validation": compiled.validation.model_dump(mode="json"),
            },
            units=payload.unit_policy.model_dump(mode="json"),
            variation_index=0,
            generation_status="completed",
            cadquery_version=getattr(__import__("cadquery"), "__version__", "unknown"),
        )
        pending_records: list[tuple] = []
        for artifact in artifacts:
            metadata = artifact.metadata
            object_key = build_object_key(
                current_user["id"], experiment_id, design_model_id, artifact.path.name
            )
            storage.save_file(object_key, artifact.path)
            saved_keys.append(object_key)
            pending_records.append((metadata, object_key))
        for metadata, object_key in pending_records:
            repo.record_design_file(
                design_id=metadata.artifact_id,
                owner_id=current_user["id"],
                experiment_id=experiment_id,
                design_model_id=design_model_id,
                file_format=metadata.file_format,
                storage_provider="supabase" if type(storage).__name__ == "SupabaseStorage" else "local",
                object_key=object_key,
                file_size_bytes=metadata.byte_size,
                checksum_sha256=metadata.checksum_sha256,
                media_type=metadata.media_type,
            )
    except Exception:
        for object_key in saved_keys:
            storage.delete_file(object_key)
        if design_model_id is not None:
            repo.update_design_model_status(design_model_id, "failed")
        logger.error("Failed to persist CAD V2 artifacts", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="The validated design could not be stored; no artifact is available.",
        )
    finally:
        for artifact in artifacts:
            artifact.path.unlink(missing_ok=True)

    assert experiment_id is not None and design_model_id is not None
    return DesignV2CompileResponse(
        design_id=design_model_id,
        experiment_id=experiment_id,
        metadata=compiled.metadata(),
    )


@router.post(
    "/design-space/preview",
    response_model=DesignSpacePreviewResponse,
    summary="Preview a deterministic resolved design space",
)
def preview_design_space(payload: DesignSpaceRequest) -> DesignSpacePreviewResponse:
    try:
        variants = build_design_space(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    method = "grid" if len(payload.parameters) == 2 else payload.parameters[0].method
    return DesignSpacePreviewResponse(
        method=method,
        seed=payload.seed,
        variant_count=len(variants),
        variants=variants,
    )


@router.post(
    "/generate-single",
    response_model=GenerateSingleResponse,
    summary="Generate a single parametric model",
    description="Parses the prompt and exports STL/STEP files for one design, durably stored via FileStorage.",
)
def generate_single(payload: PromptRequest, current_user: dict = Depends(get_current_user)):
    try:
        params = parse_design_request(payload.prompt)
    except GeometryClassificationError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc
    try:
        result = generate_model(params)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    repo = get_repository()
    storage = get_storage()
    try:
        experiment_id = repo.create_experiment(
            user_id=current_user["id"],
            name=f"generate-single: {params.geometry_type}",
            input_specification={"prompt": payload.prompt, "params": params.model_dump()},
        )
        _upload_generated_files(repo, storage, result["design_id"], current_user["id"], experiment_id, result)
    except Exception:
        logger.error("Failed to persist ownership for generated design %s", result["design_id"], exc_info=True)
        _cleanup_generated_files(result)
        raise HTTPException(
            status_code=500,
            detail="Design was generated but could not be recorded; it has not been saved. Please retry.",
        )
    return GenerateSingleResponse(
        design_id=result["design_id"],
        experiment_id=experiment_id,
        params=result["params"],
        stl_object_key=build_object_key(current_user["id"], experiment_id, result["design_id"], f"{result['design_id']}.stl"),
        step_object_key=build_object_key(current_user["id"], experiment_id, result["design_id"], f"{result['design_id']}.step"),
    )


@router.post(
    "/generate-matrix",
    summary="Generate design matrix in parallel",
    description="Builds multiple design variations using process-based parallel generation.",
)
def generate_matrix(request: DesignVariationRequest, current_user: dict = Depends(get_current_user)):
    """Generates the full Design Matrix in parallel (Module 1 -> Module 2 handoff)."""
    results = generate_design_matrix(request)
    repo = get_repository()
    storage = get_storage()
    try:
        experiment_id = repo.create_experiment(
            user_id=current_user["id"],
            name="generate-matrix batch",
            input_specification={"base_params": request.base_params.model_dump(), "variation_count": request.variation_count},
        )
        for design in results:
            if "design_id" in design and design.get("stl_path"):
                _upload_generated_files(repo, storage, design["design_id"], current_user["id"], experiment_id, design)
                design["stl_object_key"] = build_object_key(
                    current_user["id"], experiment_id, design["design_id"], f"{design['design_id']}.stl"
                )
                design["step_object_key"] = build_object_key(
                    current_user["id"], experiment_id, design["design_id"], f"{design['design_id']}.step"
                )
            design.pop("stl_path", None)
            design.pop("step_path", None)
    except Exception:
        logger.error("Failed to persist ownership for a design matrix batch", exc_info=True)
        for design in results:
            _cleanup_generated_files(design)
        raise HTTPException(
            status_code=500,
            detail="Design matrix was generated but could not be recorded; it has not been saved. Please retry.",
        )
    return results


@router.post(
    "/generate-batch",
    response_model=BatchGenerateResponse,
    status_code=202,
    summary="Queue an async batch design generation job",
    description=(
        "Creates an experiment + a persisted generation_jobs record and dispatches the batch to Celery. "
        "Returns immediately with a job_id for polling via /api/jobs/{job_id}."
    ),
)
def generate_batch(
    payload: BatchGenerateRequest,
    current_user: dict = Depends(get_current_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if payload.variation_count > settings.MAX_BATCH_VARIANTS:
        raise HTTPException(
            status_code=422,
            detail=f"variation_count exceeds the maximum allowed batch size ({settings.MAX_BATCH_VARIANTS})",
        )

    repo = get_repository()

    if idempotency_key:
        existing = repo.get_job_by_idempotency_key(current_user["id"], idempotency_key)
        if existing is not None:
            return BatchGenerateResponse(
                job_id=existing.id, experiment_id=existing.experiment_id, status=existing.status
            )

    if repo.count_active_jobs_for_user(current_user["id"]) >= settings.MAX_CONCURRENT_JOBS_PER_USER:
        raise HTTPException(
            status_code=429,
            detail=(
                "Concurrent job limit reached "
                f"({settings.MAX_CONCURRENT_JOBS_PER_USER} queued or running jobs per user)"
            ),
        )

    if payload.experiment_id:
        experiment = repo.get_experiment(payload.experiment_id)
        if experiment is None or experiment.user_id != current_user["id"]:
            raise HTTPException(status_code=404, detail="Study not found")
        if experiment.status == "archived":
            raise HTTPException(status_code=409, detail="Archived studies cannot accept new designs")
        if repo.list_design_models_for_experiment(experiment.id):
            raise HTTPException(
                status_code=409,
                detail="This study already has a persisted design space; create a new study to preserve lineage.",
            )
        experiment_id = experiment.id
    else:
        experiment_id = repo.create_experiment(
            user_id=current_user["id"],
            name=f"generate-batch: {payload.base_params.geometry_type}",
            input_specification=payload.model_dump(mode="json"),
        )

    resolved_variants = None
    requested_count = payload.variation_count
    if payload.sweep_parameters:
        try:
            resolved_variants = build_design_space(DesignSpaceRequest(
                base_params=payload.base_params,
                parameters=payload.sweep_parameters,
                seed=payload.seed,
            ))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        requested_count = len(resolved_variants)
    if payload.experiment_id:
        updated_specification = dict(experiment.input_specification)
        study_metadata = dict(updated_specification.get("study") or {})
        study_metadata["design_space"] = {
            "seed": payload.seed,
            "sweep_parameters": [item.model_dump(mode="json") for item in payload.sweep_parameters],
            "variant_count": requested_count,
            "base_params": payload.base_params.model_dump(mode="json"),
        }
        updated_specification["study"] = study_metadata
        repo.update_experiment(experiment_id, status="active", input_specification=updated_specification)
    job_id = repo.create_job(
        experiment_id=experiment_id,
        user_id=current_user["id"],
        job_type="design_batch",
        requested_count=requested_count,
        idempotency_key=idempotency_key,
    )

    # Local import avoids importing Celery/the task module (and therefore
    # requiring a broker connection at import time) for every request to
    # this router's other, synchronous endpoints.
    from app.module1_design.tasks import generate_batch_task

    try:
        generate_batch_task.delay(
            job_id=job_id,
            experiment_id=experiment_id,
            user_id=current_user["id"],
            base_params=payload.base_params.model_dump(),
            variation_count=requested_count,
            vary_fields=payload.vary_fields,
            variation_range_pct=payload.variation_range_pct,
            resolved_variants=resolved_variants,
        )
    except Exception as exc:
        repo.update_job(
            job_id, status="failed", progress_percent=100,
            error_code="worker_dispatch_failed",
            safe_error_message="The design worker is unavailable; no variant was generated.",
        )
        raise HTTPException(status_code=503, detail="The design worker is unavailable") from exc

    return BatchGenerateResponse(job_id=job_id, experiment_id=experiment_id, status="queued")


@router.get(
    "/export/{design_id}",
    summary="Download generated STL",
    description="Returns STL file by design id, streamed through the FileStorage abstraction.",
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

    storage = get_storage()
    if not storage.file_exists(record.object_key):
        raise HTTPException(status_code=404, detail="STL file not found for the given design_id")

    return storage.create_download_response(
        record.object_key, download_filename=f"{design_id}.stl", media_type="model/stl"
    )


@router.get("/files/{file_id}/download", summary="Download an owner-scoped CAD artifact")
def download_design_file(file_id: str, current_user: dict = Depends(get_current_user)):
    if not _is_valid_design_id(file_id):
        raise HTTPException(status_code=404, detail="Design artifact not found")
    record = get_repository().get_design_file(file_id)
    if record is None or record.owner_id != current_user["id"]:
        raise HTTPException(status_code=404, detail="Design artifact not found")
    storage = get_storage()
    if not storage.file_exists(record.object_key):
        raise HTTPException(status_code=404, detail="Design artifact not found")
    return storage.create_download_response(
        record.object_key,
        download_filename=f"{record.design_model_id or file_id}.{record.file_format}",
        media_type=record.media_type,
    )


