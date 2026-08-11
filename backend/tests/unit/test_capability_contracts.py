import pytest
from fastapi.testclient import TestClient

from app.capability_validation import capability_consistency_errors, validate_capability_consistency
from app.core.auth import get_current_user
from app.main import app
from app.module1_design.capability_registry import GeometryClassificationError
from app.module1_design.nl_parser import parse_design_request
from app.module2_simulation.solver_registry import SOLVER_REGISTRY
from app.module2_simulation.schemas import ImplementationStatus
from app.module3_analysis.capability_registry import ANALYSIS_CAPABILITY_REGISTRY
from app.module3_analysis.intelligence import correlations
from app.module3_analysis.schemas import DatasetQualityReport, DatasetRow, ExperimentDataset


def test_authoritative_capability_contracts_are_consistent():
    assert capability_consistency_errors() == []
    validate_capability_consistency()


def test_every_real_solver_has_a_complete_contract():
    for solver in SOLVER_REGISTRY.values():
        if solver.implementation_status == ImplementationStatus.REAL:
            assert solver.version and solver.governing_equations and solver.known_limitations
            assert solver.numerical_method != "not_available"
            assert solver.discretization != "not_available"
            assert solver.supported_geometry and solver.geometry_dependency_description
            assert isinstance(solver.consumes_cad_geometry, bool)
            assert solver.validity_envelope and solver.convergence_requirements != "not_available"
            assert solver.implementation_reference != "not_available"


def test_recognised_unimplemented_geometry_cannot_execute():
    with pytest.raises(GeometryClassificationError, match="unsupported") as exc:
        parse_design_request("Generate a dome with a base of 10 m")
    assert exc.value.code == "UNDERSTOOD_BUT_UNSUPPORTED"


@pytest.mark.parametrize(("prompt", "code"), [
    ("Generate a sphere with a radius of 1 m", "INVALID"),
    ("Generate something 10 m high", "AMBIGUOUS"),
    ("Generate a pyramid tower", "AMBIGUOUS"),
])
def test_non_executable_or_ambiguous_geometry_never_defaults_to_pyramid(prompt, code):
    with pytest.raises(GeometryClassificationError) as exc:
        parse_design_request(prompt)
    assert exc.value.code == code


def _dataset(values):
    rows = [DatasetRow(design_id=None, simulation_id=f"s-{i}", solver_id="test", solver_version="1",
                       values={"x": x, "y": y}, converged=True, simulation_status="completed", evidence_ids=[])
            for i, (x, y) in enumerate(values)]
    return ExperimentDataset(experiment_id="test", rows=rows, columns=["x", "y"], units={"x": "m", "y": "m"},
                             quality=DatasetQualityReport(source_simulation_count=len(rows), valid_row_count=len(rows),
                                                          excluded_row_count=0), dataset_hash="test")


def test_correlation_contract_and_implementation_require_three_pairs():
    for method_id in ("pearson_correlation", "spearman_correlation"):
        assert "3 pairwise-valid" in ANALYSIS_CAPABILITY_REGISTRY[method_id]["minimum_sample_rules"]
        assert {"coefficient", "p_value", "sample_count", "evidence_simulation_ids"} <= set(
            ANALYSIS_CAPABILITY_REGISTRY[method_id]["outputs"]
        )
    assert correlations(_dataset([(1, 2), (2, 4)]))["relationships"] == []
    assert len(correlations(_dataset([(1, 2), (2, 4), (3, 6)]))["relationships"]) == 1


def test_sensitivity_contract_matches_implementation_rule():
    assert ANALYSIS_CAPABILITY_REGISTRY["standardized_linear_regression_sensitivity"]["minimum_sample_rules"] == (
        "At least max(5, number_of_features + 2) complete rows."
    )


def test_execution_mode_metadata_matches_bounded_solver_implementations():
    cfd = SOLVER_REGISTRY["cfd_laminar_channel_2d_v1"]
    acoustic = SOLVER_REGISTRY["acoustic_duct_1d_v1"]
    thermal = SOLVER_REGISTRY["thermal_conduction_v1"]
    assert "finite-difference" in cfd.numerical_method.lower()
    assert "numpy.linalg.solve" in cfd.numerical_method
    assert "finite-difference" in acoustic.numerical_method.lower()
    assert "complex direct numpy.linalg.solve" in acoustic.numerical_method
    assert "1D: finite-difference assembled linear system with direct numpy.linalg.solve" in thermal.numerical_method
    assert "3D: Gauss-Seidel-style iterative" in thermal.numerical_method


def test_validator_detects_missing_solver_contract_field(monkeypatch):
    monkeypatch.setattr(SOLVER_REGISTRY["thermal_conduction_v1"], "numerical_method", "not_available")
    assert any("numerical_method" in error for error in capability_consistency_errors())


def test_validator_detects_malformed_analysis_contract(monkeypatch):
    monkeypatch.setitem(ANALYSIS_CAPABILITY_REGISTRY["pearson_correlation"], "outputs", [])
    assert any("analysis capability malformed: pearson_correlation" == error for error in capability_consistency_errors())


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
