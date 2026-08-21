"""Private persistence and worker lifecycle for certified CAD-derived CFD."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app.core.repository import SimulationResultRecord
from app.core.storage import StorageError, build_object_key
from app.module1_design.cad_v2_compiler import compile_design
from app.module1_design.cad_v2_schemas import EngineeringDesignDocumentV2
from app.module2_simulation.evidence_lifecycle import persist_automatic_evidence
from app.module2_simulation.field_results import persist_field_result
from app.module2_simulation.geometry_physics_schemas import PhysicsDomain, PhysicsModelRequest, PhysicsModelV1
from app.module2_simulation.openfoam_case import (
    BACKEND_ID, BACKEND_VERSION, MASS_IMBALANCE_LIMIT, SOLVER_ID, SOLVER_VERSION,
    OpenFOAMCaseError, generate_laminar_fv_case, parse_cfd_fv_solution, validate_fv_cfd_scope,
)
from app.module2_simulation.openfoam_fv_mesh import (
    CFDGeneratedMeshV1, CFDMeshError, CFDMeshResolutionV1, _parse_poly_mesh,
    certify_final_cfd_mesh, generate_certified_cfd_surface, generate_snappyhex_case,
    run_snappyhex_mesher,
)
from app.module2_simulation.physics_model import PhysicsValidationError, build_cfd_physics_model
from app.module2_simulation.solver_orchestrator import OpenFOAMAdapterFoundation, OpenFOAMExecutionConfig
from app.module2_simulation.solver_registry import SOLVER_REGISTRY

logger = logging.getLogger(__name__)
POLYMESH_FILES = ("points", "faces", "owner", "neighbour", "boundary")
MAX_POLYMESH_BUNDLE_BYTES = 128 * 1024 * 1024


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CFDPhysicsCreateRequest(_Strict):
    experiment_id: str
    document: EngineeringDesignDocumentV2
    domains: list[PhysicsDomain] = Field(min_length=1, max_length=1)
    resolution: CFDMeshResolutionV1
    physics: PhysicsModelRequest


class CFDExecutionRequest(_Strict):
    solver_id: Literal["cfd_openfoam_laminar_internal_3d_v1"] = SOLVER_ID


class CFDPreparationResponse(_Strict):
    preparation_id: str
    status: Literal["queued", "running", "completed", "failed"]
    physics_model: PhysicsModelV1 | None = None
    mesh: CFDGeneratedMeshV1 | None = None
    error_code: str | None = None
    safe_error_message: str | None = None


class CFDExecutionError(ValueError):
    def __init__(self, code: str, message: str, simulation_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.simulation_id = simulation_id


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def private_cfd_id(owner_id: str, kind: str, scientific_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"asre-lab:{owner_id}:{kind}:{scientific_id}"))


def _persist_bytes(*, repository, storage, owner_id: str, experiment_id: str, record_id: str,
                   filename: str, data: bytes, file_format: str, media_type: str) -> None:
    checksum = hashlib.sha256(data).hexdigest()
    existing = repository.get_design_file(record_id)
    if existing is not None:
        if existing.owner_id != owner_id or existing.experiment_id != experiment_id or existing.checksum_sha256 != checksum:
            raise CFDExecutionError("CFD_ARTIFACT_IDENTITY_CONFLICT", "A private CFD artifact identity conflicts with persisted science")
        if not storage.file_exists(existing.object_key):
            with tempfile.TemporaryDirectory(prefix="asre-cfd-repair-") as directory:
                path = Path(directory) / Path(filename).name
                path.write_bytes(data)
                storage.save_file(existing.object_key, path)
        return
    object_key = build_object_key(owner_id, experiment_id, record_id, Path(filename).name)
    try:
        with tempfile.TemporaryDirectory(prefix="asre-cfd-artifact-") as directory:
            path = Path(directory) / Path(filename).name
            path.write_bytes(data)
            storage.save_file(object_key, path)
        repository.record_design_file(
            design_id=record_id, owner_id=owner_id, experiment_id=experiment_id,
            design_model_id=None, file_format=file_format,
            storage_provider="supabase" if type(storage).__name__ == "SupabaseStorage" else "local",
            object_key=object_key, file_size_bytes=len(data), checksum_sha256=checksum, media_type=media_type,
        )
    except Exception:
        storage.delete_file(object_key)
        raise


def _load_bytes(*, repository, storage, owner_id: str, record_id: str) -> bytes:
    record = repository.get_design_file(record_id)
    if record is None or record.owner_id != owner_id:
        raise CFDExecutionError("CFD_ARTIFACT_NOT_FOUND", "Private CFD artifact is unavailable")
    data = storage.open_bytes(record.object_key)
    if record.checksum_sha256 is None or hashlib.sha256(data).hexdigest() != record.checksum_sha256:
        raise CFDExecutionError("CFD_ARTIFACT_CHECKSUM_FAILED", "Private CFD artifact failed integrity verification")
    return data


def _poly_mesh_bundle(case: Path) -> bytes:
    root = case / "constant" / "polyMesh"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in POLYMESH_FILES:
            data = (root / name).read_bytes()
            info = zipfile.ZipInfo(f"constant/polyMesh/{name}", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, data)
    bundle = output.getvalue()
    if len(bundle) > MAX_POLYMESH_BUNDLE_BYTES:
        raise CFDExecutionError("CFD_MESH_BUNDLE_TOO_LARGE", "Certified CFD mesh bundle exceeds the storage limit")
    return bundle


def persist_cfd_mesh(*, repository, storage, owner_id: str, experiment_id: str,
                     mesh: CFDGeneratedMeshV1, case: Path) -> None:
    _persist_bytes(
        repository=repository, storage=storage, owner_id=owner_id, experiment_id=experiment_id,
        record_id=private_cfd_id(owner_id, "cfd-mesh-bundle", mesh.mesh_id),
        filename=f"{mesh.mesh_id}.polymesh.zip", data=_poly_mesh_bundle(case),
        file_format="openfoam_polymesh_v1", media_type="application/zip",
    )
    _persist_bytes(
        repository=repository, storage=storage, owner_id=owner_id, experiment_id=experiment_id,
        record_id=private_cfd_id(owner_id, "cfd-mesh-metadata", mesh.mesh_id),
        filename=f"{mesh.mesh_id}.cfd-mesh.json", data=mesh.model_dump_json().encode(),
        file_format="cfd_generated_mesh_v1", media_type="application/json",
    )


def load_cfd_mesh(*, repository, storage, owner_id: str, mesh_id: str,
                  case: Path | None = None) -> CFDGeneratedMeshV1:
    metadata = _load_bytes(repository=repository, storage=storage, owner_id=owner_id,
                           record_id=private_cfd_id(owner_id, "cfd-mesh-metadata", mesh_id))
    try:
        mesh = CFDGeneratedMeshV1.model_validate_json(metadata)
    except Exception as exc:
        raise CFDExecutionError("CFD_MESH_METADATA_INVALID", "Persisted CFD mesh metadata is invalid") from exc
    if mesh.mesh_id != mesh_id:
        raise CFDExecutionError("CFD_MESH_IDENTITY_MISMATCH", "Persisted CFD mesh identity is inconsistent")
    if case is None:
        return mesh
    bundle = _load_bytes(repository=repository, storage=storage, owner_id=owner_id,
                         record_id=private_cfd_id(owner_id, "cfd-mesh-bundle", mesh_id))
    if len(bundle) > MAX_POLYMESH_BUNDLE_BYTES:
        raise CFDExecutionError("CFD_MESH_BUNDLE_INVALID", "Persisted CFD mesh bundle exceeds the storage limit")
    try:
        with zipfile.ZipFile(io.BytesIO(bundle), "r") as archive:
            expected = {f"constant/polyMesh/{name}" for name in POLYMESH_FILES}
            if (set(archive.namelist()) != expected
                    or sum(info.file_size for info in archive.infolist()) > MAX_POLYMESH_BUNDLE_BYTES):
                raise CFDExecutionError("CFD_MESH_BUNDLE_INVALID", "Persisted CFD mesh bundle has an invalid manifest")
            for name in POLYMESH_FILES:
                target = case / "constant" / "polyMesh" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(f"constant/polyMesh/{name}"))
        poly = _parse_poly_mesh(case)
    except CFDExecutionError:
        raise
    except Exception as exc:
        raise CFDExecutionError("CFD_MESH_BUNDLE_INVALID", "Persisted CFD mesh bundle is unreadable") from exc
    cell_count = max((*poly.owners, *poly.neighbours)) + 1
    if cell_count != mesh.cell_count or len(poly.faces) != mesh.face_count or len(poly.neighbours) != mesh.internal_face_count:
        raise CFDExecutionError("CFD_MESH_BUNDLE_MISMATCH", "Persisted polyMesh counts do not match certified metadata")
    for patch in mesh.semantic_patches:
        persisted_patch = poly.patches.get(patch.final_patch)
        if patch.start_face is None or persisted_patch is None or persisted_patch[:2] != (patch.start_face, patch.final_face_count):
            raise CFDExecutionError("CFD_MESH_BUNDLE_MISMATCH", "Persisted polyMesh semantic identity does not match certification")
    return mesh


def persist_cfd_physics(*, repository, storage, owner_id: str, experiment_id: str,
                        model: PhysicsModelV1) -> None:
    _persist_bytes(
        repository=repository, storage=storage, owner_id=owner_id, experiment_id=experiment_id,
        record_id=private_cfd_id(owner_id, "physics-model", model.physics_model_id),
        filename=f"{model.physics_model_id}.physics.json", data=model.model_dump_json().encode(),
        file_format="physics_model_v1", media_type="application/json",
    )


def load_cfd_physics(*, repository, storage, owner_id: str, physics_model_id: str) -> PhysicsModelV1:
    data = _load_bytes(repository=repository, storage=storage, owner_id=owner_id,
                       record_id=private_cfd_id(owner_id, "physics-model", physics_model_id))
    try:
        return PhysicsModelV1.model_validate_json(data)
    except Exception as exc:
        raise CFDExecutionError("CFD_PHYSICS_INVALID", "Persisted CFD PhysicsModel is invalid") from exc


def prepare_cfd_physics(*, repository, storage, job_id: str, owner_id: str,
                        payload: CFDPhysicsCreateRequest) -> CFDPreparationResponse:
    job = repository.get_job(job_id)
    if job is None or job.user_id != owner_id:
        raise CFDExecutionError("CFD_PREPARATION_NOT_FOUND", "CFD preparation job is unavailable")
    repository.update_job(job_id, status="running", progress_percent=5, started_at=_now())
    try:
        if payload.physics.domains != payload.domains:
            raise CFDExecutionError("CFD_DOMAIN_MISMATCH", "CFD request and PhysicsModel domains must match")
        domain = payload.domains[0]
        categories = {}
        for boundary in payload.physics.boundary_conditions:
            category = {"velocity_inlet": "inlet", "pressure_boundary": "outlet", "wall": "wall"}.get(boundary.bc_type)
            if category is None:
                raise CFDExecutionError("UNSUPPORTED_CFD_BOUNDARY", "CFD supports one velocity inlet, pressure outlet, and wall group")
            categories[boundary.semantic_region] = category
        compiled = compile_design(payload.document)
        with tempfile.TemporaryDirectory(prefix="asre-cfd-prepare-") as directory:
            case = Path(directory)
            surface = generate_certified_cfd_surface(compiled, domain, categories)
            generated = generate_snappyhex_case(case, surface, payload.resolution)
            _, _, check_log = run_snappyhex_mesher(case, payload.resolution)
            mesh = certify_final_cfd_mesh(compiled, domain, generated, case, check_log)
            model = build_cfd_physics_model(mesh, payload.physics)
            persist_cfd_mesh(repository=repository, storage=storage, owner_id=owner_id,
                             experiment_id=payload.experiment_id, mesh=mesh, case=case)
            persist_cfd_physics(repository=repository, storage=storage, owner_id=owner_id,
                                experiment_id=payload.experiment_id, model=model)
        result = CFDPreparationResponse(preparation_id=job_id, status="completed", physics_model=model, mesh=mesh)
        _persist_bytes(
            repository=repository, storage=storage, owner_id=owner_id, experiment_id=payload.experiment_id,
            record_id=private_cfd_id(owner_id, "cfd-preparation-result", job_id),
            filename=f"{job_id}.cfd-preparation.json", data=result.model_dump_json().encode(),
            file_format="cfd_preparation_result_v1", media_type="application/json",
        )
        repository.update_job(job_id, status="completed", completed_count=1, progress_percent=100, finished_at=_now())
        return result
    except (CFDExecutionError, CFDMeshError, PhysicsValidationError, OpenFOAMCaseError, ValueError) as exc:
        code = getattr(exc, "code", "CFD_PREPARATION_FAILED")
        repository.update_job(job_id, status="failed", failed_count=1, progress_percent=100,
                              error_code=code, safe_error_message="CFD preparation failed scientific validation.", finished_at=_now())
        return CFDPreparationResponse(preparation_id=job_id, status="failed", error_code=code,
                                      safe_error_message="CFD preparation failed scientific validation.")
    except Exception:
        logger.error("CFD preparation %s failed", job_id, exc_info=True)
        repository.update_job(job_id, status="failed", failed_count=1, progress_percent=100,
                              error_code="CFD_PREPARATION_FAILED", safe_error_message="CFD preparation failed unexpectedly.", finished_at=_now())
        return CFDPreparationResponse(preparation_id=job_id, status="failed", error_code="CFD_PREPARATION_FAILED",
                                      safe_error_message="CFD preparation failed unexpectedly.")


def get_cfd_preparation(*, repository, storage, owner_id: str, preparation_id: str) -> CFDPreparationResponse:
    job = repository.get_job(preparation_id)
    if job is None or job.user_id != owner_id or job.job_type != "cfd_physics_preparation":
        raise CFDExecutionError("CFD_PREPARATION_NOT_FOUND", "CFD preparation job is unavailable")
    if job.status == "completed":
        data = _load_bytes(repository=repository, storage=storage, owner_id=owner_id,
                           record_id=private_cfd_id(owner_id, "cfd-preparation-result", preparation_id))
        return CFDPreparationResponse.model_validate_json(data)
    return CFDPreparationResponse(preparation_id=preparation_id, status=job.status,
                                  error_code=job.error_code, safe_error_message=job.safe_error_message)


def create_cfd_preparation_job(*, repository, storage, owner_id: str,
                               payload: CFDPhysicsCreateRequest, idempotency_key: str) -> tuple[str, bool]:
    request_data = payload.model_dump(mode="json")
    request_hash = _hash(request_data)
    existing = repository.get_job_by_idempotency_key(owner_id, idempotency_key)
    if existing is not None:
        if existing.job_type != "cfd_physics_preparation" or existing.experiment_id != payload.experiment_id:
            raise CFDExecutionError("CFD_IDEMPOTENCY_CONFLICT", "Idempotency key belongs to a different preparation")
        persisted = json.loads(_load_bytes(
            repository=repository, storage=storage, owner_id=owner_id,
            record_id=private_cfd_id(owner_id, "cfd-preparation-request", existing.id),
        ))
        if persisted.get("request_hash") != request_hash:
            raise CFDExecutionError("CFD_IDEMPOTENCY_CONFLICT", "Idempotency key belongs to different CFD science")
        return existing.id, False
    job_id = repository.create_job(payload.experiment_id, owner_id, "cfd_physics_preparation", 1, idempotency_key)
    _persist_bytes(
        repository=repository, storage=storage, owner_id=owner_id, experiment_id=payload.experiment_id,
        record_id=private_cfd_id(owner_id, "cfd-preparation-request", job_id),
        filename=f"{job_id}.cfd-request.json",
        data=json.dumps({"request_hash": request_hash, "request": request_data}, sort_keys=True,
                        separators=(",", ":")).encode(),
        file_format="cfd_preparation_request_v1", media_type="application/json",
    )
    return job_id, True


def _scientific_input(model: PhysicsModelV1, mesh: CFDGeneratedMeshV1) -> dict:
    return {
        "solver_id": SOLVER_ID, "solver_version": SOLVER_VERSION,
        "backend_id": BACKEND_ID, "backend_version": BACKEND_VERSION,
        "physics_model_id": model.physics_model_id, "physics_hash": model.physics_hash,
        "mesh_id": mesh.mesh_id, "mesh_hash": mesh.mesh_hash, "source_surface_hash": mesh.source_surface_hash,
        "design_hash": mesh.design_hash, "geometry_fingerprint": mesh.geometry_fingerprint,
        "domains": [item.model_dump(mode="json") for item in model.domains],
        "materials": [item.model_dump(mode="json") for item in model.materials],
        "material_assignments": [item.model_dump(mode="json") for item in model.material_assignments],
        "boundary_conditions": [item.model_dump(mode="json") for item in model.boundary_conditions],
        "numerical_settings": model.numerical_settings.model_dump(mode="json"),
    }


def create_cad_cfd_execution(*, repository, storage, user_id: str, experiment_id: str,
                             model: PhysicsModelV1, mesh: CFDGeneratedMeshV1,
                             idempotency_key: str) -> tuple[str, bool]:
    payload = _scientific_input(model, mesh)
    fingerprint = _hash(payload)
    existing = repository.get_simulation_job_by_idempotency_key(user_id, idempotency_key)
    if existing is not None:
        previous = repository.get_simulation_input(existing.id)
        if existing.solver_id != SOLVER_ID or existing.experiment_id != experiment_id or previous is None or _hash(previous.geometry) != fingerprint:
            raise CFDExecutionError("CFD_IDEMPOTENCY_CONFLICT", "Idempotency key belongs to different CFD science", existing.id)
        return existing.id, False
    simulation_id = repository.create_simulation_job(user_id, SOLVER_ID, experiment_id, None, idempotency_key)
    repository.record_simulation_input(
        simulation_id, model.materials[0].material_name,
        {item.material_name: item.model_dump(mode="json") for item in model.materials},
        {"length": "m", "velocity": "m/s", "pressure": "m2/s2"}, {},
        payload["boundary_conditions"], payload["numerical_settings"], payload,
    )
    try:
        validate_fv_cfd_scope(mesh, model)
    except Exception as exc:
        code = getattr(exc, "code", "CFD_PREFLIGHT_FAILED")
        repository.update_simulation_job(simulation_id, status="failed", progress_percent=100,
                                         error_code=code, safe_error_message="CFD mesh and physics identities do not match.", finished_at=_now())
        raise CFDExecutionError(code, "CFD mesh and physics identities do not match", simulation_id) from exc
    return simulation_id, True


def _field_axes(values: np.ndarray) -> list[dict]:
    axes = [{"name": "cell_id", "unit": "index", "values": list(range(values.shape[0]))}]
    if values.ndim == 2:
        axes.append({"name": "component", "unit": "index", "values": list(range(values.shape[1]))})
    return axes


def execute_cad_cfd_job(*, repository, storage, simulation_id: str) -> dict:
    job = repository.get_simulation_job(simulation_id)
    if job is None or job.solver_id != SOLVER_ID:
        raise CFDExecutionError("CFD_JOB_NOT_FOUND", "CFD simulation job is unavailable")
    if job.status == "cancelled":
        return {"simulation_id": simulation_id, "status": "cancelled"}
    existing = repository.get_simulation_result(simulation_id)
    if existing is not None:
        records = persist_automatic_evidence(repository, simulation_id)
        return {"simulation_id": simulation_id, "status": existing.status, "evidence_record_count": len(records)}
    if repository.list_field_results(simulation_id):
        repository.update_simulation_job(simulation_id, status="failed", progress_percent=100,
                                         error_code="CFD_PARTIAL_FIELD_LIFECYCLE",
                                         safe_error_message="A prior CFD field lifecycle was incomplete.")
        return {"simulation_id": simulation_id, "status": "failed"}
    simulation_input = repository.get_simulation_input(simulation_id)
    if simulation_input is None:
        raise CFDExecutionError("CFD_INPUT_MISSING", "Persisted CFD input is unavailable")
    payload = simulation_input.geometry
    repository.update_simulation_job(simulation_id, status="running", progress_percent=10, started_at=_now())
    started = time.monotonic()
    try:
        model = load_cfd_physics(repository=repository, storage=storage, owner_id=job.user_id,
                                 physics_model_id=payload["physics_model_id"])
        adapter = OpenFOAMAdapterFoundation(OpenFOAMExecutionConfig(
            timeout_seconds=min(int(payload["numerical_settings"]["maximum_iterations"]) * 2, 3600)))
        with adapter.case_workspace() as case:
            mesh = load_cfd_mesh(repository=repository, storage=storage, owner_id=job.user_id,
                                 mesh_id=payload["mesh_id"], case=case)
            if _hash(_scientific_input(model, mesh)) != _hash(payload):
                raise CFDExecutionError("CFD_INPUT_IDENTITY_MISMATCH", "Persisted CFD input no longer matches its artifacts")
            definition = generate_laminar_fv_case(mesh, model, case)
            completed = adapter.run_fixed_case(case)
            solution = parse_cfd_fv_solution(mesh, model, definition, case, completed.stdout + completed.stderr)
        finite_fields = bool(solution.fields["U"].finite and solution.fields["p"].finite and solution.flux.finite)
        tolerance = float(model.numerical_settings.tolerance)
        combined = max(solution.diagnostics.final_u_residual / tolerance,
                       solution.diagnostics.final_p_residual / tolerance,
                       solution.diagnostics.normalized_mass_imbalance / MASS_IMBALANCE_LIMIT)
        if not solution.converged or not finite_fields or combined > 1.0:
            raise CFDExecutionError("CFD_NOT_CONVERGED", "CFD solve did not meet the combined convergence criteria")
        u_values = np.asarray(solution.fields["U"].values, dtype=float)
        p_values = np.asarray(solution.fields["p"].values, dtype=float)
        field_records = [
            persist_field_result(
                repository=repository, storage=storage, user_id=job.user_id,
                experiment_id=job.experiment_id or "unassigned", simulation_id=simulation_id,
                variable_name="U", unit="m/s", axes=_field_axes(u_values), values=u_values,
                solver_id=SOLVER_ID, solver_version=SOLVER_VERSION,
                grid_metadata={"location_type": "cell_centered", "mesh_hash": mesh.mesh_hash,
                               "quantity": "velocity", "component_order": "Ux,Uy,Uz"},
            ),
            persist_field_result(
                repository=repository, storage=storage, user_id=job.user_id,
                experiment_id=job.experiment_id or "unassigned", simulation_id=simulation_id,
                variable_name="p", unit="m2/s2", axes=_field_axes(p_values), values=p_values,
                solver_id=SOLVER_ID, solver_version=SOLVER_VERSION,
                grid_metadata={"location_type": "cell_centered", "mesh_hash": mesh.mesh_hash,
                               "quantity": "kinematic_pressure", "physical_pressure_conversion": "rho * p",
                               "density_kg_m3": definition.density_kg_m3,
                               "density_source": definition.density_source},
            ),
        ]
        metadata = {
            "input_fingerprint": _hash(payload),
            "material_properties_used": {
                f"{material.material_name}.{property_.name}": property_.value
                for material in model.materials for property_ in material.properties
            },
            "validation_status": SOLVER_REGISTRY[SOLVER_ID].validation_status.value,
            "physics_model_hash": model.physics_hash, "mesh_id": mesh.mesh_id, "mesh_hash": mesh.mesh_hash,
            "source_surface_hash": mesh.source_surface_hash, "design_hash": mesh.design_hash,
            "geometry_fingerprint": mesh.geometry_fingerprint, "case_fingerprint": definition.case_fingerprint,
            "backend_id": BACKEND_ID, "backend_version": BACKEND_VERSION,
            "convergence_metric": "cfd_combined_residual_mass_conservation",
            "convergence_conditions": {
                "simple_converged": solution.converged,
                "final_u_residual": solution.diagnostics.final_u_residual, "u_tolerance": tolerance,
                "final_p_residual": solution.diagnostics.final_p_residual, "p_tolerance": tolerance,
                "normalized_mass_imbalance": solution.diagnostics.normalized_mass_imbalance,
                "mass_imbalance_limit": MASS_IMBALANCE_LIMIT, "finite_reviewed_fields": finite_fields,
            },
            "server_validation": {"benchmark_id": "cfd_square_duct_poiseuille_v1",
                                  "trust_level": "moderate", "user_benchmark_evidence": False},
            "pressure_interpretation": solution.pressure_interpretation.model_dump(mode="json"),
        }
        reproducibility = _hash({"input": payload, "case": definition.case_fingerprint,
                                 "summary": solution.summary_metrics, "combined": combined,
                                 "fields": [item.checksum_sha256 for item in field_records]})
        repository.record_simulation_result(SimulationResultRecord(
            simulation_id=simulation_id, solver_id=SOLVER_ID, solver_version=SOLVER_VERSION,
            governing_equations=SOLVER_REGISTRY[SOLVER_ID].governing_equations,
            assumptions=["steady", "incompressible", "Newtonian", "single-phase", "isothermal", "laminar", "fixed geometry", "internal flow"],
            warnings=list(SOLVER_REGISTRY[SOLVER_ID].known_limitations), converged=True,
            residual=combined, iteration_count=solution.iterations, tolerance=1.0,
            summary_metrics=solution.summary_metrics, result_object_keys=[item.storage_object_key for item in field_records],
            status="completed", numerical_method="OpenFOAM Foundation 14 foamRun incompressibleFluid SIMPLE finite volume",
            validation_metadata=metadata, elapsed_time_seconds=time.monotonic() - started,
            reproducibility_hash=reproducibility,
        ))
        repository.update_simulation_job(simulation_id, status="completed", progress_percent=100, finished_at=_now())
        evidence = persist_automatic_evidence(repository, simulation_id)
        return {"simulation_id": simulation_id, "status": "completed",
                "field_result_count": len(field_records), "evidence_record_count": len(evidence)}
    except (CFDExecutionError, CFDMeshError, PhysicsValidationError, OpenFOAMCaseError, StorageError, ValueError) as exc:
        code = getattr(exc, "code", "CFD_EXECUTION_FAILED")
        repository.update_simulation_job(simulation_id, status="failed", progress_percent=100,
                                         error_code=code, safe_error_message="The CFD solve failed scientific validation.", finished_at=_now())
        return {"simulation_id": simulation_id, "status": "failed", "error_code": code}
    except Exception:
        logger.error("CFD simulation %s failed", simulation_id, exc_info=True)
        repository.update_simulation_job(simulation_id, status="failed", progress_percent=100,
                                         error_code="CFD_EXECUTION_FAILED",
                                         safe_error_message="The CFD solve failed unexpectedly. No result was produced.", finished_at=_now())
        return {"simulation_id": simulation_id, "status": "failed", "error_code": "CFD_EXECUTION_FAILED"}
