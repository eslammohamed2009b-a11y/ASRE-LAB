from __future__ import annotations

import hashlib
import io
import uuid

import numpy as np
import pytest

from app.core.repository import FieldResultRecord, LocalSQLiteRepository
from app.core.storage import LocalFileStorage, StorageError
from app.module2_simulation.field_results import (
    FieldResultValidationError,
    load_field_artifact,
    persist_field_result,
    save_field_artifact,
)
from app.module2_simulation import service

pytestmark = pytest.mark.unit


def _axes():
    return [{"name": "x", "unit": "m", "values": [0.0, 0.5, 1.0]}]


def test_npz_field_artifact_is_deterministic_safe_and_checksummed(tmp_path):
    storage = LocalFileStorage(tmp_path / "objects")
    values = np.array([10.0, 20.0, 30.0])
    artifact = save_field_artifact(
        storage=storage, user_id="user-a", experiment_id="exp-a", simulation_id="sim-a",
        variable_name="temperature", unit="degC", axes=_axes(), values=values,
    )
    raw = storage.open_bytes(artifact.object_key)
    assert hashlib.sha256(raw).hexdigest() == artifact.checksum_sha256
    assert artifact.shape == [3]
    assert artifact.minimum == 10.0
    assert artifact.maximum == 30.0
    assert artifact.mean == 20.0
    assert np.array_equal(load_field_artifact(storage, artifact.object_key, artifact.checksum_sha256), values)
    with np.load(io.BytesIO(raw), allow_pickle=False) as archive:
        assert list(archive.files) == ["field"]


def test_field_validation_rejects_shape_units_and_path_traversal(tmp_path):
    storage = LocalFileStorage(tmp_path / "objects")
    with pytest.raises(FieldResultValidationError):
        save_field_artifact(
            storage=storage, user_id="user-a", experiment_id="exp-a", simulation_id="sim-a",
            variable_name="temperature", unit="", axes=_axes(), values=np.array([1.0, 2.0, 3.0]),
        )
    with pytest.raises(FieldResultValidationError):
        save_field_artifact(
            storage=storage, user_id="user-a", experiment_id="exp-a", simulation_id="sim-a",
            variable_name="temperature", unit="K", axes=_axes(), values=np.array([1.0, 2.0]),
        )
    with pytest.raises(StorageError):
        storage.open_bytes("../../secret.npz")


def test_corrupt_artifact_fails_closed(tmp_path):
    storage = LocalFileStorage(tmp_path / "objects")
    artifact = save_field_artifact(
        storage=storage, user_id="user-a", experiment_id="exp-a", simulation_id="sim-a",
        variable_name="pressure", unit="Pa", axes=_axes(), values=np.array([1.0, 2.0, 3.0]),
    )
    assert artifact.checksum_sha256
    with pytest.raises(StorageError):
        load_field_artifact(storage, artifact.object_key, "0" * 64)


def test_persist_field_result_cleans_object_when_database_write_fails(tmp_path):
    storage = LocalFileStorage(tmp_path / "objects")

    class FailingRepository:
        def record_field_result(self, _record):
            raise RuntimeError("database unavailable")

    with pytest.raises(RuntimeError):
        persist_field_result(
            repository=FailingRepository(), storage=storage, user_id="user-a", experiment_id="exp-a",
            simulation_id="sim-a", variable_name="temperature", unit="K", axes=_axes(),
            values=np.array([1.0, 2.0, 3.0]), solver_id="thermal", solver_version="1",
        )
    assert not list((tmp_path / "objects").rglob("*.npz"))


def test_field_metadata_survives_repository_reload(tmp_path):
    db_path = tmp_path / "field-results.db"
    repo = LocalSQLiteRepository(db_path)
    simulation_id = repo.create_simulation_job(user_id="user-a", solver_id="thermal_conduction_v1")
    record = FieldResultRecord(
        id=str(uuid.uuid4()), simulation_id=simulation_id, user_id="user-a",
        variable_name="temperature", unit="K", format="numpy_npz", format_version="1",
        dimensions=1, axes=_axes(), array_shape=[3], grid_metadata={"kind": "structured"},
        storage_object_key=f"users/user-a/experiments/exp-a/simulations/{simulation_id}/temperature.npz",
        checksum_sha256="a" * 64, byte_size=128, minimum=1.0, maximum=3.0, mean=2.0,
        preview=[1.0, 2.0, 3.0], reproducibility_hash="b" * 64, created_at="2026-07-21T00:00:00+00:00",
    )
    repo.record_field_result(record)
    reloaded = LocalSQLiteRepository(db_path)
    assert reloaded.get_field_result(record.id) == record
    assert reloaded.list_field_results(simulation_id) == [record]


def test_field_metadata_service_is_owner_scoped(tmp_path, monkeypatch):
    repo = LocalSQLiteRepository(tmp_path / "owner.db")
    simulation_id = repo.create_simulation_job(user_id="user-a", solver_id="thermal_conduction_v1")
    record = FieldResultRecord(
        id=str(uuid.uuid4()), simulation_id=simulation_id, user_id="user-a", variable_name="temperature",
        unit="K", format="numpy_npz", format_version="1", dimensions=1, axes=_axes(), array_shape=[3],
        storage_object_key=f"users/user-a/experiments/exp-a/simulations/{simulation_id}/temperature.npz",
        checksum_sha256="a" * 64, byte_size=128, minimum=1.0, maximum=3.0, mean=2.0,
        preview=[1.0, 2.0, 3.0], reproducibility_hash="b" * 64,
    )
    repo.record_field_result(record)
    monkeypatch.setattr(service, "get_repository", lambda: repo)
    assert service.list_field_results_service(simulation_id, "user-a")[0].id == record.id
    with pytest.raises(service.SimulationNotFoundError):
        service.list_field_results_service(simulation_id, "user-b")
    with pytest.raises(service.SimulationNotFoundError):
        service.get_field_result_service(simulation_id, record.id, "user-b")
