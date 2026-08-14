"""Authenticated additive API for CAD-derived meshes and PhysicsModelV1."""
from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from app.core.auth import get_current_user
from app.core.repository import get_repository
from app.core.storage import StorageError, build_object_key, get_storage
from app.module1_design.cad_v2_compiler import CADCompilationError, compile_design
from app.module2_simulation.geometry_physics_schemas import (
    GeometryPreparationResult,
    MeshArtifact,
    MeshCreateRequest,
    PhysicsCreateRequest,
    PhysicsModelV1,
    PhysicsExecutionRequest,
    PhysicsExecutionResult,
)
from app.module2_simulation.meshing import GeneratedMesh, MeshingError, generate_mesh, prepare_geometry, write_gmsh22
from app.module2_simulation.physics_model import PhysicsValidationError, build_physics_model
from app.module2_simulation.cad_fem_execution import FEMExecutionError, execute_cad_fem
from app.module2_simulation.fem_core import FEMError


router = APIRouter(
    prefix="/api/geometry-physics",
    tags=["Authoritative CAD to Physics"],
    dependencies=[Depends(get_current_user)],
)


def _owned_experiment(experiment_id: str, owner_id: str):
    experiment = get_repository().get_experiment(experiment_id)
    if experiment is None or experiment.user_id != owner_id:
        raise HTTPException(status_code=404, detail="Study not found")
    if experiment.status == "archived":
        raise HTTPException(status_code=422, detail="Archived studies cannot accept new artifacts")
    return experiment


def _compile_or_422(document):
    try:
        return compile_design(document)
    except CADCompilationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc), "feature_id": exc.feature_id, "body_id": exc.body_id},
        ) from exc


def _scientific_error(exc: MeshingError | PhysicsValidationError) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)})


def _private_id(owner_id: str, kind: str, scientific_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"asre-lab:{owner_id}:{kind}:{scientific_id}"))


def _persist_bytes(
    *, owner_id: str, experiment_id: str, record_id: str, filename: str,
    data: bytes, file_format: str, media_type: str,
) -> None:
    repo = get_repository()
    storage = get_storage()
    existing = repo.get_design_file(record_id)
    if existing is not None:
        if existing.owner_id != owner_id:
            raise HTTPException(status_code=404, detail="Artifact not found")
        if not storage.file_exists(existing.object_key):
            with tempfile.TemporaryDirectory(prefix="asre-artifact-repair-") as directory:
                scratch = Path(directory) / filename
                scratch.write_bytes(data)
                storage.save_file(existing.object_key, scratch)
        return
    object_key = build_object_key(owner_id, experiment_id, record_id, filename)
    try:
        with tempfile.TemporaryDirectory(prefix="asre-artifact-") as directory:
            scratch = Path(directory) / filename
            scratch.write_bytes(data)
            checksum = storage.calculate_checksum(scratch)
            storage.save_file(object_key, scratch)
        repo.record_design_file(
            design_id=record_id,
            owner_id=owner_id,
            experiment_id=experiment_id,
            design_model_id=None,
            file_format=file_format,
            storage_provider="supabase" if type(storage).__name__ == "SupabaseStorage" else "local",
            object_key=object_key,
            file_size_bytes=len(data),
            checksum_sha256=checksum,
            media_type=media_type,
        )
    except (StorageError, OSError) as exc:
        storage.delete_file(object_key)
        raise HTTPException(status_code=503, detail="The private scientific artifact could not be stored") from exc


def _persist_mesh(mesh, experiment_id: str, owner_id: str) -> MeshArtifact:
    artifact_id = _private_id(owner_id, "mesh-artifact", mesh.metadata.mesh_id)
    metadata_id = _private_id(owner_id, "mesh-metadata", mesh.metadata.mesh_id)
    public_metadata = mesh.metadata.model_copy(update={"artifact_id": artifact_id})
    with tempfile.TemporaryDirectory(prefix="asre-mesh-serialize-") as directory:
        path = Path(directory) / f"{mesh.metadata.mesh_id}.msh"
        write_gmsh22(mesh, path)
        msh_bytes = path.read_bytes()
    _persist_bytes(
        owner_id=owner_id, experiment_id=experiment_id, record_id=artifact_id,
        filename=f"{mesh.metadata.mesh_id}.msh", data=msh_bytes,
        file_format="msh", media_type="application/vnd.gmsh",
    )
    _persist_bytes(
        owner_id=owner_id, experiment_id=experiment_id, record_id=metadata_id,
        filename=f"{mesh.metadata.mesh_id}.mesh.json",
        data=public_metadata.model_dump_json().encode("utf-8"),
        file_format="mesh_metadata", media_type="application/json",
    )
    return public_metadata


def _persist_physics(model: PhysicsModelV1, experiment_id: str, owner_id: str) -> None:
    record_id = _private_id(owner_id, "physics-model", model.physics_model_id)
    _persist_bytes(
        owner_id=owner_id, experiment_id=experiment_id, record_id=record_id,
        filename=f"{model.physics_model_id}.physics.json",
        data=model.model_dump_json().encode("utf-8"),
        file_format="physics_model_v1", media_type="application/json",
    )


def _load_json(record_id: str, owner_id: str) -> dict:
    repo = get_repository()
    storage = get_storage()
    record = repo.get_design_file(record_id)
    if record is None or record.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="Scientific record not found")
    try:
        return json.loads(storage.open_bytes(record.object_key))
    except (StorageError, ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=503, detail="Scientific record is temporarily unavailable") from exc


def _load_mesh(mesh_id: str, owner_id: str) -> GeneratedMesh:
    metadata = MeshArtifact.model_validate(_load_json(_private_id(owner_id, "mesh-metadata", mesh_id), owner_id))
    artifact = get_repository().get_design_file(metadata.artifact_id or "")
    if artifact is None or artifact.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="Scientific record not found")
    try:
        lines = get_storage().open_bytes(artifact.object_key).decode("utf-8").splitlines()
        nodes_start = lines.index("$Nodes") + 2; nodes_end = lines.index("$EndNodes")
        nodes_by_id = {int(parts[0]): tuple(float(value) for value in parts[1:4]) for parts in (line.split() for line in lines[nodes_start:nodes_end])}
        if sorted(nodes_by_id) != list(range(1, len(nodes_by_id) + 1)):
            raise ValueError("Mesh node identities are not contiguous")
        elements_start = lines.index("$Elements") + 2; elements_end = lines.index("$EndElements")
        triangles, tetrahedra = [], []
        for line in lines[elements_start:elements_end]:
            values = [int(value) for value in line.split()]; kind, tags = values[1], values[2]; vertices = tuple(value - 1 for value in values[3 + tags:])
            if kind == 2: triangles.append(vertices)
            elif kind == 4: tetrahedra.append(vertices)
        return GeneratedMesh(metadata=metadata, nodes_m=tuple(nodes_by_id[index] for index in range(1, len(nodes_by_id) + 1)),
            tetrahedra=tuple(tetrahedra), boundary_facets=tuple(triangles))
    except (StorageError, ValueError, UnicodeError, IndexError) as exc:
        raise HTTPException(status_code=503, detail="Stored authoritative mesh is invalid or unavailable") from exc


@router.post("/geometry/prepare", response_model=GeometryPreparationResult)
def prepare_cad_geometry(payload: MeshCreateRequest, current_user: dict = Depends(get_current_user)):
    _owned_experiment(payload.experiment_id, current_user["id"])
    return prepare_geometry(_compile_or_422(payload.document), payload.domains)


@router.post("/meshes/validate", response_model=MeshArtifact)
def validate_cad_mesh(payload: MeshCreateRequest, current_user: dict = Depends(get_current_user)):
    _owned_experiment(payload.experiment_id, current_user["id"])
    try:
        return generate_mesh(_compile_or_422(payload.document), payload.domains, payload.specification).metadata
    except MeshingError as exc:
        raise _scientific_error(exc) from exc


@router.post("/meshes", response_model=MeshArtifact)
def create_cad_mesh(payload: MeshCreateRequest, current_user: dict = Depends(get_current_user)):
    _owned_experiment(payload.experiment_id, current_user["id"])
    try:
        mesh = generate_mesh(_compile_or_422(payload.document), payload.domains, payload.specification)
        return _persist_mesh(mesh, payload.experiment_id, current_user["id"])
    except (MeshingError, PhysicsValidationError) as exc:
        raise _scientific_error(exc) from exc


@router.get("/meshes/{mesh_id}", response_model=MeshArtifact)
def get_cad_mesh(mesh_id: str, current_user: dict = Depends(get_current_user)):
    record_id = _private_id(current_user["id"], "mesh-metadata", mesh_id)
    try:
        return MeshArtifact.model_validate(_load_json(record_id, current_user["id"]))
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Stored mesh metadata is invalid") from exc


@router.post("/physics/validate", response_model=PhysicsModelV1)
def validate_physics(payload: PhysicsCreateRequest, current_user: dict = Depends(get_current_user)):
    _owned_experiment(payload.experiment_id, current_user["id"])
    try:
        mesh = generate_mesh(_compile_or_422(payload.document), payload.domains, payload.specification)
        return build_physics_model(mesh, payload.physics)
    except (MeshingError, PhysicsValidationError) as exc:
        raise _scientific_error(exc) from exc


@router.post("/physics", response_model=PhysicsModelV1)
def create_physics(payload: PhysicsCreateRequest, current_user: dict = Depends(get_current_user)):
    _owned_experiment(payload.experiment_id, current_user["id"])
    try:
        mesh = generate_mesh(_compile_or_422(payload.document), payload.domains, payload.specification)
        _persist_mesh(mesh, payload.experiment_id, current_user["id"])
        model = build_physics_model(mesh, payload.physics)
        _persist_physics(model, payload.experiment_id, current_user["id"])
        return model
    except (MeshingError, PhysicsValidationError) as exc:
        raise _scientific_error(exc) from exc


@router.get("/physics/{physics_model_id}", response_model=PhysicsModelV1)
def get_physics(physics_model_id: str, current_user: dict = Depends(get_current_user)):
    record_id = _private_id(current_user["id"], "physics-model", physics_model_id)
    try:
        return PhysicsModelV1.model_validate(_load_json(record_id, current_user["id"]))
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Stored physics metadata is invalid") from exc


@router.post("/physics/{physics_model_id}/execute", response_model=PhysicsExecutionResult)
def execute_physics(physics_model_id: str, payload: PhysicsExecutionRequest,
                    idempotency_key: str = Header(..., alias="Idempotency-Key"),
                    current_user: dict = Depends(get_current_user)):
    try:
        model = PhysicsModelV1.model_validate(_load_json(_private_id(current_user["id"], "physics-model", physics_model_id), current_user["id"]))
        mesh = _load_mesh(model.mesh_id, current_user["id"])
        mesh_record = get_repository().get_design_file(_private_id(current_user["id"], "mesh-metadata", model.mesh_id))
        if mesh_record is None:
            raise HTTPException(status_code=404, detail="Scientific record not found")
        simulation_id = execute_cad_fem(repository=get_repository(), storage=get_storage(), user_id=current_user["id"],
            experiment_id=mesh_record.experiment_id, design_id=None, mesh=mesh, model=model,
            solver_id=payload.solver_id, idempotency_key=idempotency_key)
        job = get_repository().get_simulation_job(simulation_id)
        return PhysicsExecutionResult(simulation_id=simulation_id, solver_id=payload.solver_id, status=job.status if job else "failed")
    except FEMExecutionError as exc:
        raise HTTPException(status_code=422, detail={"code": "FEM_EXECUTION_REJECTED", "message": str(exc)}) from exc
    except FEMError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("/artifacts/{artifact_id}/download", response_class=Response)
def download_mesh_artifact(artifact_id: str, current_user: dict = Depends(get_current_user)):
    record = get_repository().get_design_file(artifact_id)
    if record is None or record.owner_id != current_user["id"] or record.file_format != "msh":
        raise HTTPException(status_code=404, detail="Artifact not found")
    return get_storage().create_download_response(
        record.object_key, f"{artifact_id}.msh", "application/vnd.gmsh"
    )
