import pytest

from app import study_router
from app.core.repository import LocalSQLiteRepository
from app.v2.repository import EvidenceRepository


pytestmark = pytest.mark.integration


@pytest.fixture
def study_repo(tmp_path, monkeypatch):
    path = tmp_path / "studies.sqlite3"
    repository = LocalSQLiteRepository(path)
    monkeypatch.setattr(study_router, "get_repository", lambda: repository)
    monkeypatch.setattr(study_router, "EvidenceRepository", lambda: EvidenceRepository(path))
    return repository


def setup_payload(title="Pyramid thermal comparison"):
    return {
        "title": title,
        "description": "Controlled geometry study",
        "research_question": "How does pyramid height affect the bounded thermal result?",
        "hypothesis": "Peak temperature changes with height under held-constant conditions.",
        "geometry_family": "pyramid",
        "independent_variables": [{"code": "height_m", "label": "Height", "unit": "m"}],
        "output_variables": [{"code": "max_temperature_c", "label": "Peak temperature", "unit": "degC"}],
        "controlled_variables": [{"code": "material", "value": "concrete"}],
        "solver_ids": ["pyramid_thermal_conduction_v1"],
        "material": "concrete",
        "boundary_conditions": {"ambient_temperature_c": 20, "heat_source_w_m3": 100},
        "numerical_settings": {"grid_resolution": 17, "tolerance": 1e-6},
    }


def test_create_list_update_and_reopen_server_persisted_study(authorized_client, study_repo):
    created = authorized_client.post("/api/studies", json=setup_payload())
    assert created.status_code == 201
    study_id = created.json()["id"]
    assert created.json()["status"] == "draft"

    detail = authorized_client.get(f"/api/studies/{study_id}")
    assert detail.status_code == 200
    assert [item["record_type"] for item in detail.json()["evidence"]] == ["study_definition"]

    listed = authorized_client.get("/api/studies")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [study_id]

    updated = authorized_client.patch(
        f"/api/studies/{study_id}", json={"title": "Renamed study", "status": "active"}
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Renamed study"

    # Construct a fresh repository instance to prove state is not client memory.
    reopened_repository = LocalSQLiteRepository(study_repo.db_path)
    study_router.get_repository = lambda: reopened_repository
    reopened = authorized_client.get(f"/api/studies/{study_id}")
    assert reopened.status_code == 200
    assert reopened.json()["research_question"].startswith("How does pyramid height")
    assert reopened.json()["designs"] == []


def test_study_owner_isolation_returns_indistinguishable_404(authorized_client, study_repo):
    other_id = study_repo.create_experiment(
        "different-user", "Private other study", {"study": setup_payload("Other")}
    )
    response = authorized_client.get(f"/api/studies/{other_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Study not found"


def test_list_studies_excludes_legacy_experiments_and_preserves_archived_filtering(authorized_client, study_repo, monkeypatch):
    active = authorized_client.post("/api/studies", json=setup_payload("Active research study"))
    archived = authorized_client.post("/api/studies", json=setup_payload("Archived research study"))
    assert active.status_code == archived.status_code == 201
    archived_id = archived.json()["id"]
    study_repo.update_experiment(archived_id, status="archived")
    legacy_id = study_repo.create_experiment(
        "test-user", "generate-single: GeometryType.PYRAMID", {"prompt": "legacy geometry generation"},
    )
    assert authorized_client.get(f"/api/studies/{active.json()['id']}").status_code == 200
    monkeypatch.setattr(study_router, "_study_graph", lambda _: (_ for _ in ()).throw(AssertionError("list loaded full graph")))

    listed = authorized_client.get("/api/studies")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [active.json()["id"]]

    including_archived = authorized_client.get("/api/studies?include_archived=true")
    assert including_archived.status_code == 200
    assert {item["id"] for item in including_archived.json()["items"]} == {active.json()["id"], archived_id}
    assert study_repo.get_experiment(legacy_id) is not None
