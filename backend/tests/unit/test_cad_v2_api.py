from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.main import app
from app.module1_design.cad_v2_compiler import CAD_V2_CAPABILITY_CONTRACT
from app.module1_design.cad_v2_schemas import DesignV2CompileResponse


def _payload():
    return {
        "document_id": "api_plate",
        "parameters": [{"name": "width", "parameter_type": "length", "value": 100, "unit": "mm"}],
        "bodies": [{"body_id": "plate"}],
        "sketches": [{"sketch_id": "profile", "entities": [{
            "entity_type": "rectangle", "entity_id": "outline",
            "width": {"parameter": "width"}, "height": {"value": 50, "unit": "mm"},
        }]}],
        "features": [{
            "operation": "extrude", "feature_id": "make_plate", "sketch_id": "profile",
            "output_body": "plate", "distance": {"value": 10, "unit": "mm"},
        }],
        "output_body_ids": ["plate"],
    }


def test_v2_capability_and_validation_endpoints_are_authenticated_and_truthful():
    unauthenticated = TestClient(app).get("/api/design/v2/capabilities")
    assert unauthenticated.status_code == 401
    app.dependency_overrides[get_current_user] = lambda: {"id": "cad-v2-user"}
    try:
        client = TestClient(app)
        capability = client.get("/api/design/v2/capabilities")
        validation = client.post("/api/design/v2/validate", json=_payload())
    finally:
        app.dependency_overrides.clear()
    assert capability.status_code == 200
    assert capability.json() == CAD_V2_CAPABILITY_CONTRACT
    assert any(
        item.startswith("No general sketch constraint solver")
        for item in capability.json()["known_limitations"]
    )
    assert validation.status_code == 200
    assert validation.json()["valid"] is True
    assert len(validation.json()["design_hash"]) == 64


def test_v2_compile_contract_exposes_only_opaque_artifact_ids_not_storage_keys():
    schema_text = str(DesignV2CompileResponse.model_json_schema()).lower()
    assert "object_key" not in schema_text
    assert "storage_path" not in schema_text
    assert "signed_url" not in schema_text
    openapi = app.openapi()
    operation = openapi["paths"]["/api/design/v2/compile"]["post"]
    assert operation["security"] == [{"OAuth2PasswordBearer": []}]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DesignV2CompileResponse"
    }
