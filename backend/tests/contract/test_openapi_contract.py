from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.main import app

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]


def test_frozen_openapi_contract() -> None:
    snapshot = json.loads((ROOT / "openapi-contract.json").read_text())
    spec = app.openapi()
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode()).hexdigest() == snapshot["openapi_sha256"]
    assert len(spec["paths"]) == snapshot["path_count"]
    assert set(snapshot["required_paths"]) <= set(spec["paths"])

    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            if path.startswith("/api/"):
                assert operation.get("security") == [{"OAuth2PasswordBearer": []}], (path, method)
    for path in snapshot["public_paths"]:
        assert spec["paths"][path]["get"].get("security") is None
    for path in spec["paths"]:
        if path.startswith("/api/simulate/"):
            assert all(operation.get("deprecated") is True for operation in spec["paths"][path].values())

    typed_responses = {
        ("/api/couplings/thermal-structural", "post"): "ThermalStructuralCouplingResponse",
        ("/api/couplings/{coupling_id}", "get"): "ThermalStructuralCouplingResponse",
        ("/api/design-feedback/proposals", "post"): "ProposalResponse",
        ("/api/design-feedback/proposals/{proposal_id}/execute", "post"): "IterationResponse",
    }
    for (path, method), schema_name in typed_responses.items():
        schema = spec["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema == {"$ref": f"#/components/schemas/{schema_name}"}
