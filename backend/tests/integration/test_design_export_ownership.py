"""
Real, executed proof that one user cannot download another user's
exported design file (see app.module1_design.ownership_store for why this
check was missing before and what it fixes).

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
