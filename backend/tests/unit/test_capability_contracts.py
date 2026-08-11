import pytest
from fastapi.testclient import TestClient

from app.capability_validation import capability_consistency_errors, validate_capability_consistency
from app.core.auth import get_current_user
from app.main import app
from app.module1_design.capability_registry import UnsupportedRecognizedGeometryError
from app.module1_design.nl_parser import parse_design_request
from app.module2_simulation.solver_registry import SOLVER_REGISTRY
from app.module2_simulation.schemas import ImplementationStatus


def test_authoritative_capability_contracts_are_consistent():
    assert capability_consistency_errors() == []
    validate_capability_consistency()


def test_every_real_solver_has_a_complete_contract():
    for solver in SOLVER_REGISTRY.values():
        if solver.implementation_status == ImplementationStatus.REAL:
            assert solver.version and solver.governing_equations and solver.known_limitations


def test_recognised_unimplemented_geometry_cannot_execute():
    with pytest.raises(UnsupportedRecognizedGeometryError):
        parse_design_request("Generate a dome with a base of 10 m")


def test_public_capability_api_is_registry_derived():
    app.dependency_overrides[get_current_user] = lambda: {"id": "capability-test"}
    try:
        response = TestClient(app).get("/api/capabilities")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()
    assert {item["solver_id"] for item in body["simulation"]} == set(SOLVER_REGISTRY)
    assert {item["geometry_id"] for item in body["design"]} == {"pyramid", "tower", "bridge", "arch", "dome"}
