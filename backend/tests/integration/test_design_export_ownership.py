"""
Real, executed proof that one user cannot download another user's
exported design file (see app.core.repository for the durable
persistence/ownership abstraction this exercises, and why the check
matters).

This drives the actual FastAPI app with the real CadQuery kernel over
TestClient (no stub), using two different authenticated identities.
"""
import pytest

from app.core.auth import get_current_user
from app.main import app

pytestmark = pytest.mark.integration


def _client_as(user_id: str):
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_current_user] = lambda: {"id": user_id, "role": "researcher"}
    return TestClient(app)


def test_user_b_cannot_download_user_a_design():
    try:
        client_a = _client_as("user-a")
        generate_response = client_a.post(
            "/api/design/generate-single",
            json={"prompt": "granite pyramid 120 meters tall"},
        )
        assert generate_response.status_code == 200
        design_id = generate_response.json()["design_id"]

        # Owner can download their own file.
        own_download = client_a.get(f"/api/design/export/{design_id}")
        assert own_download.status_code == 200

        # A different authenticated user must NOT be able to download it.
        client_b = _client_as("user-b")
        other_download = client_b.get(f"/api/design/export/{design_id}")
        assert other_download.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_unknown_design_id_is_rejected_even_with_valid_auth():
    try:
        client_a = _client_as("user-a")
        response = client_a.get("/api/design/export/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_malformed_design_id_is_rejected_not_500():
    """A non-uuid path segment must fail closed (404), not 400/500 - callers
    should not be able to distinguish "not a valid id" from "not found"."""
    try:
        client_a = _client_as("user-a")
        response = client_a.get("/api/design/export/not-a-uuid")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_path_traversal_attempt_in_design_id_is_rejected():
    try:
        client_a = _client_as("user-a")
        response = client_a.get("/api/design/export/..%2F..%2F..%2Fetc%2Fpasswd")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_ownership_survives_repository_reconstruction():
    """Simulates durability across a process restart / a second API
    instance: a brand new repository object (re-reading the same backing
    SQLite file) still enforces the same ownership decision."""
    from app.core.repository import get_repository

    try:
        client_a = _client_as("user-a")
        generate_response = client_a.post(
            "/api/design/generate-single",
            json={"prompt": "granite pyramid 90 meters tall"},
        )
        assert generate_response.status_code == 200
        design_id = generate_response.json()["design_id"]

        # A fresh repository instance (as a restarted process / another
        # replica would construct) must see the same persisted record.
        repo = get_repository()
        record = repo.get_design_file(design_id)
        assert record is not None
        assert record.owner_id == "user-a"

        client_b = _client_as("user-b")
        other_download = client_b.get(f"/api/design/export/{design_id}")
        assert other_download.status_code == 404
    finally:
        app.dependency_overrides.clear()
