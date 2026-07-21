"""Bounded, deterministic scientific field artifacts.

Large numerical fields live in compressed NPZ objects; only validated metadata
and small previews belong in the database/API. Loading always disables pickle.
"""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.core.storage import FileStorage, StorageError, build_simulation_object_key
from app.core.repository import FieldResultRecord, PersistenceRepository

FORMAT = "numpy_npz"
FORMAT_VERSION = "1"
MAX_DIMENSIONS = 4
MAX_ARRAY_ELEMENTS = 2_000_000
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
MAX_PREVIEW_VALUES = 256
MAX_RESIDUAL_VALUES = 5_000


class FieldResultValidationError(ValueError):
    pass


@dataclass(frozen=True)
class FieldArtifact:
    object_key: str
    checksum_sha256: str
    byte_size: int
    shape: list[int]
    minimum: float
    maximum: float
    mean: float
    preview: list[float]
    reproducibility_hash: str


def _validate_array(values: np.ndarray, unit: str, axes: list[dict]) -> np.ndarray:
    array = np.asarray(values)
    if not unit or len(unit) > 64:
        raise FieldResultValidationError("A concise physical unit is required")
    if array.ndim < 1 or array.ndim > MAX_DIMENSIONS:
        raise FieldResultValidationError("Field dimensionality is outside the supported limit")
    if array.size < 1 or array.size > MAX_ARRAY_ELEMENTS:
        raise FieldResultValidationError("Field array size is outside the supported limit")
    if len(axes) != array.ndim:
        raise FieldResultValidationError("Coordinate axis count must match field dimensions")
    if not np.issubdtype(array.dtype, np.number) or not np.isfinite(array).all():
        raise FieldResultValidationError("Field values must be finite numeric values")
    for size, axis in zip(array.shape, axes):
        coords = axis.get("values", [])
        if len(coords) != size or len(coords) > MAX_ARRAY_ELEMENTS:
            raise FieldResultValidationError("Each coordinate axis must match its field dimension")
        if not axis.get("name") or not axis.get("unit"):
            raise FieldResultValidationError("Coordinate axes require names and units")
    return np.asarray(array, dtype=np.float64)


def _reproducibility_hash(array: np.ndarray, metadata: dict) -> str:
    digest = hashlib.sha256()
    digest.update(array.astype("<f8", copy=False).tobytes(order="C"))
    digest.update(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode())
    return digest.hexdigest()


def save_field_artifact(
    *, storage: FileStorage, user_id: str, experiment_id: str,
    simulation_id: str, variable_name: str, unit: str, axes: list[dict],
    values: np.ndarray,
) -> FieldArtifact:
    array = _validate_array(values, unit, axes)
    safe_variable = "".join(c if c.isalnum() or c in "_-" else "_" for c in variable_name)[:80]
    if not safe_variable:
        raise FieldResultValidationError("Field variable name is required")
    metadata = {"variable_name": variable_name, "unit": unit, "axes": axes, "shape": list(array.shape)}
    buffer = io.BytesIO()
    np.savez_compressed(buffer, field=array)
    data = buffer.getvalue()
    if len(data) > MAX_ARTIFACT_BYTES:
        raise FieldResultValidationError("Compressed field artifact exceeds the size limit")
    checksum = hashlib.sha256(data).hexdigest()
    key = build_simulation_object_key(
        user_id, experiment_id, simulation_id, f"{safe_variable}-{uuid.uuid4().hex}.npz"
    )
    with tempfile.NamedTemporaryFile(suffix=".npz", delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        storage.save_file(key, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    flat = array.ravel(order="C")
    step = max(1, int(np.ceil(flat.size / MAX_PREVIEW_VALUES)))
    preview = flat[::step][:MAX_PREVIEW_VALUES].tolist()
    return FieldArtifact(
        object_key=key, checksum_sha256=checksum, byte_size=len(data),
        shape=list(array.shape), minimum=float(array.min()), maximum=float(array.max()),
        mean=float(array.mean()), preview=preview,
        reproducibility_hash=_reproducibility_hash(array, metadata),
    )


def load_field_artifact(storage: FileStorage, object_key: str, checksum_sha256: str) -> np.ndarray:
    data = storage.open_bytes(object_key)
    if len(data) > MAX_ARTIFACT_BYTES or hashlib.sha256(data).hexdigest() != checksum_sha256:
        raise StorageError("Field artifact failed integrity verification")
    try:
        with np.load(io.BytesIO(data), allow_pickle=False) as archive:
            array = np.asarray(archive["field"], dtype=np.float64)
    except Exception as exc:
        raise StorageError("Field artifact is invalid or unreadable") from exc
    if array.size > MAX_ARRAY_ELEMENTS or array.ndim > MAX_DIMENSIONS or not np.isfinite(array).all():
        raise StorageError("Field artifact violates configured safety limits")
    return array


def persist_field_result(
    *, repository: PersistenceRepository, storage: FileStorage, user_id: str,
    experiment_id: str, simulation_id: str, variable_name: str, unit: str,
    axes: list[dict], values: np.ndarray, solver_id: str, solver_version: str,
    grid_metadata: dict | None = None,
) -> FieldResultRecord:
    """Atomically-as-practical store an artifact and its database metadata.

    A failed metadata insert triggers compensating object cleanup so callers
    do not silently accumulate orphaned scientific results.
    """
    artifact = save_field_artifact(
        storage=storage, user_id=user_id, experiment_id=experiment_id,
        simulation_id=simulation_id, variable_name=variable_name, unit=unit,
        axes=axes, values=values,
    )
    record = FieldResultRecord(
        id=str(uuid.uuid4()), simulation_id=simulation_id, user_id=user_id,
        variable_name=variable_name, unit=unit, format=FORMAT, format_version=FORMAT_VERSION,
        dimensions=len(artifact.shape), axes=axes, array_shape=artifact.shape,
        grid_metadata={**(grid_metadata or {}), "solver_id": solver_id, "solver_version": solver_version},
        storage_object_key=artifact.object_key, checksum_sha256=artifact.checksum_sha256,
        byte_size=artifact.byte_size, minimum=artifact.minimum, maximum=artifact.maximum,
        mean=artifact.mean, preview=artifact.preview, reproducibility_hash=artifact.reproducibility_hash,
    )
    try:
        repository.record_field_result(record)
    except Exception:
        storage.delete_file(artifact.object_key)
        raise
    return repository.get_field_result(record.id) or record
