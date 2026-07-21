import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.repository import LocalSQLiteRepository
from app.core.storage import LocalFileStorage
from app.main import app
from app.module2_simulation.field_results import persist_field_result

pytestmark = pytest.mark.integration


def _client(user_id: str) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: {"id": user_id, "role": "researcher"}
    return TestClient(app)


def test_field_metadata_and_download_are_owner_scoped(tmp_path, monkeypatch):
    db_path = tmp_path / "api.db"
    storage_root = tmp_path / "objects"
    monkeypatch.setenv("LOCAL_PERSISTENCE_DB_PATH", str(db_path))
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(storage_root))
    monkeypatch.setattr(settings, "LOCAL_STORAGE_ROOT", str(storage_root))
    repo = LocalSQLiteRepository(db_path)
    simulation_id = repo.create_simulation_job(user_id="user-a", solver_id="thermal_conduction_v1")
    record = persist_field_result(
        repository=repo, storage=LocalFileStorage(storage_root), user_id="user-a", experiment_id="exp-a",
        simulation_id=simulation_id, variable_name="temperature", unit="K",
        axes=[{"name": "x", "unit": "m", "values": [0.0, 0.5, 1.0]}],
        values=np.array([293.15, 303.15, 313.15]), solver_id="thermal_conduction_v1", solver_version="1.0.0",
    )
    try:
        owner = _client("user-a")
        listed = owner.get(f"/api/simulations/{simulation_id}/fields")
        assert listed.status_code == 200
        assert listed.json()[0]["id"] == record.id
        metadata = owner.get(f"/api/simulations/{simulation_id}/fields/{record.id}")
        assert metadata.status_code == 200
        assert metadata.json()["checksum_sha256"] == record.checksum_sha256
        downloaded = owner.get(f"/api/simulations/{simulation_id}/fields/{record.id}/download")
        assert downloaded.status_code == 200
        assert downloaded.content

        other = _client("user-b")
        assert other.get(f"/api/simulations/{simulation_id}/fields").status_code == 404
        assert other.get(f"/api/simulations/{simulation_id}/fields/{record.id}").status_code == 404
        assert other.get(f"/api/simulations/{simulation_id}/fields/{record.id}/download").status_code == 404
    finally:
        app.dependency_overrides.clear()
