from __future__ import annotations

import numpy as np
import pytest

from app.module3_analysis.intelligence import correlations, regression_sensitivity
from app.module3_analysis.schemas import (
    ConstraintSpec, DOEConfig, DatasetQualityReport, DatasetRow, ExperimentDataset,
    NextExperimentRequest, SensitivitySpec,
)
from app.module3_analysis.study_engine import (
    anomaly_diagnostics, evaluate_constraints, generate_doe, next_experiments,
    response_surface, robustness,
)


def _dataset() -> ExperimentDataset:
    rows = []
    for index in range(12):
        x, y = index / 11, (index * 5 % 11) / 10
        response = 2 + 3 * x - 2 * y + 4 * x * x + 1.5 * x * y
        rows.append(DatasetRow(
            design_id=f"design-{index}", simulation_id=f"simulation-{index}", solver_id="real_fixture_solver",
            solver_version="1", converged=True, simulation_status="completed", evidence_ids=[f"evidence-{index}"],
            values={"design.x": x, "design.y": y, "metric.response": response, "metric.mass_kg": 2 - x},
        ))
    columns = sorted(rows[0].values)
    return ExperimentDataset(experiment_id="study", rows=rows, columns=columns,
        units={"design.x": "m", "design.y": "m", "metric.response": "K", "metric.mass_kg": "kg"},
        quality=DatasetQualityReport(source_simulation_count=len(rows), valid_row_count=len(rows), excluded_row_count=0), dataset_hash="a" * 64)


def _factors():
    return [
        {"name": "x", "source_path": "design.x", "unit": "m", "minimum": 0, "maximum": 1},
        {"name": "y", "source_path": "design.y", "unit": "m", "minimum": 0, "maximum": 1},
    ]


def _dense_quadratic_dataset() -> ExperimentDataset:
    rows = []
    for index, (x, y) in enumerate((x / 4, y / 4) for x in range(5) for y in range(5)):
        rows.append(DatasetRow(
            design_id=f"dense-design-{index}", simulation_id=f"dense-simulation-{index}", solver_id="real_fixture_solver",
            solver_version="1", converged=True, simulation_status="completed", evidence_ids=[f"dense-evidence-{index}"],
            values={"design.x": x, "design.y": y, "metric.response": 2 + 3*x - 2*y + 4*x*x + 1.5*x*y},
        ))
    return ExperimentDataset(experiment_id="dense-study", rows=rows, columns=sorted(rows[0].values),
        units={"design.x": "m", "design.y": "m", "metric.response": "K"},
        quality=DatasetQualityReport(source_simulation_count=len(rows), valid_row_count=len(rows), excluded_row_count=0), dataset_hash="b" * 64)


def test_fdr_regression_diagnostics_and_second_order_surface_are_deterministic():
    dataset = _dataset()
    association = correlations(dataset, "both")
    assert association["relationships"]
    assert all("q_value" in item["pearson"] for item in association["relationships"])
    assert all(0 <= item["pearson"]["q_value"] <= 1 for item in association["relationships"])
    sensitivity = regression_sensitivity(dataset, SensitivitySpec(target="metric.response", features=["design.x", "design.y"]))
    assert sensitivity["adjusted_r_squared"] is not None
    assert set(sensitivity["variance_inflation_factors"]) == {"design.x", "design.y"}
    assert sensitivity["bootstrap"]["seed"] == 20260401
    surface = response_surface(dataset, _factors(), "metric.response")
    assert surface["status"] == "completed"
    # Terms are deliberately on standardized-factor coordinates.
    assert surface["training_r_squared"] == pytest.approx(1.0, abs=1e-10)
    assert abs(surface["coefficients"]["design.x^2"]) > 0.1
    assert abs(surface["coefficients"]["design.x*design.y"]) > 0.01
    assert surface["cross_validation"]["folds"] <= 5


def test_doe_constraints_robustness_anomalies_and_next_points_are_bounded():
    factorial = generate_doe(_factors(), DOEConfig(method="full_factorial", levels={"x": [0, 1], "y": [0, 0.5, 1]}))
    assert factorial["requested_count"] == 6 and factorial["quality"]["duplicate_count"] == 0
    lhs_a = generate_doe(_factors(), DOEConfig(method="latin_hypercube", count=12, seed=17))
    lhs_b = generate_doe(_factors(), DOEConfig(method="latin_hypercube", count=12, seed=17))
    assert lhs_a["doe_hash"] == lhs_b["doe_hash"]
    dataset = _dataset()
    constraints = evaluate_constraints(dataset, [{"name": "mass", "column": "metric.mass_kg", "operator": "<=", "value": 1.5, "unit": "kg"}])
    assert any(item["state"] == "feasible" for item in constraints["candidates"])
    assert any(item["state"] == "infeasible" for item in constraints["candidates"])
    plan = next_experiments(dataset, _factors(), NextExperimentRequest(candidate_count=32, count=3, seed=4))
    assert len(plan["proposals"]) == 3
    assert {item["proposal_type"] for item in plan["proposals"]} == {"EXPLORE"}
    assert all(0 <= value <= 1 for proposal in plan["proposals"] for value in proposal["factor_coordinates"].values())
    replicated = dataset.model_copy(update={"rows": [*dataset.rows, dataset.rows[0].model_copy(update={"simulation_id": "replicate", "values": {**dataset.rows[0].values, "metric.response": dataset.rows[0].values["metric.response"] + 0.2}})]})
    repeatability = robustness(replicated, _factors(), [{"name": "response", "column": "metric.response", "unit": "K"}])
    assert repeatability["status"] == "completed"
    anomalous = dataset.model_copy(update={"rows": [*dataset.rows, dataset.rows[0].model_copy(update={"simulation_id": "outlier", "values": {**dataset.rows[0].values, "metric.response": 1_000_000.0}})]})
    assert anomaly_diagnostics(anomalous, ["metric.response"])["flags"]


def test_refine_uses_a_valid_surface_and_preserves_explore_contract():
    dataset = _dense_quadratic_dataset()
    surface = response_surface(dataset, _factors(), "metric.response")
    plan = next_experiments(
        dataset, _factors(), NextExperimentRequest(candidate_count=64, count=4, seed=19),
        source_analysis_id="analysis-1", response_surface_result=surface,
        declared_response_columns={"metric.response"}, study_definition_hash="definition-a",
        study_revision=2, analysis_reproducibility_hash="repro-a",
    )
    assert plan["refine_status"] == "REFINE_ELIGIBLE"
    assert any(item["proposal_type"] == "REFINE" for item in plan["proposals"])
    assert any(item["proposal_type"] == "EXPLORE" for item in plan["proposals"])
    refine = next(item for item in plan["proposals"] if item["proposal_type"] == "REFINE")
    assert "normalized_interaction_magnitude" in refine["score_components"]
    assert refine["total_score"] >= 0
    existing = {(row.values["design.x"], row.values["design.y"]) for row in dataset.rows}
    proposed = [tuple(item["factor_coordinates"].values()) for item in plan["proposals"]]
    assert not (set(proposed) & existing) and len(proposed) == len(set(proposed))
    repeated = next_experiments(
        dataset, _factors(), NextExperimentRequest(candidate_count=64, count=4, seed=19),
        source_analysis_id="analysis-1", response_surface_result=surface,
        declared_response_columns={"metric.response"}, study_definition_hash="definition-a",
        study_revision=2, analysis_reproducibility_hash="repro-a",
    )
    assert plan["proposal_hash"] == repeated["proposal_hash"]
    changed = next_experiments(
        dataset, _factors(), NextExperimentRequest(candidate_count=64, count=4, seed=19),
        source_analysis_id="analysis-1", response_surface_result=surface,
        declared_response_columns={"metric.response"}, study_definition_hash="definition-b",
        study_revision=2, analysis_reproducibility_hash="repro-a",
    )
    assert plan["proposal_hash"] != changed["proposal_hash"]


def test_invalid_surfaces_block_refine_and_discrete_candidates_are_projected():
    dataset = _dataset()
    bad_rows = [row.model_copy(update={"values": {**row.values, "design.y": row.values["design.x"]}}) for row in dataset.rows]
    rank_deficient = response_surface(dataset.model_copy(update={"rows": bad_rows}), _factors(), "metric.response")
    assert rank_deficient["status"] == "diagnostically_invalid"
    plan = next_experiments(
        dataset, [
            {"name": "x", "source_path": "design.x", "unit": "m", "minimum": 0, "maximum": 1, "allowed_values": [0, 0.5, 1]},
            _factors()[1],
        ], NextExperimentRequest(candidate_count=64, count=4, seed=7),
        response_surface_result=rank_deficient, declared_response_columns={"metric.response"},
    )
    assert plan["refine_status"] == "REFINE_NOT_APPLICABLE"
    assert {item["proposal_type"] for item in plan["proposals"]} == {"EXPLORE"}
    coordinates = [tuple(item["factor_coordinates"].values()) for item in plan["proposals"]]
    assert len(coordinates) == len(set(coordinates))
    assert all(item["factor_coordinates"]["x"] in {0, 0.5, 1} for item in plan["proposals"])
    assert all(0 <= value <= 1 for item in plan["proposals"] for value in item["factor_coordinates"].values())
    pathological = response_surface(_dense_quadratic_dataset(), _factors(), "metric.response")
    pathological["condition_number"] = 1e20
    pathological["diagnostics"]["condition_number"] = 1e20
    blocked = next_experiments(
        _dense_quadratic_dataset(), _factors(), NextExperimentRequest(candidate_count=32, count=2, seed=9),
        response_surface_result=pathological, declared_response_columns={"metric.response"},
    )
    assert blocked["refine_status"] == "REFINE_NOT_APPLICABLE"
    assert {item["proposal_type"] for item in blocked["proposals"]} == {"EXPLORE"}


def test_doe_fails_closed_for_explosion_and_invalid_levels():
    factors = [{"name": f"x{index}", "source_path": f"design.x{index}", "unit": "m", "minimum": 0, "maximum": 1} for index in range(10)]
    with pytest.raises(ValueError, match="500"):
        generate_doe(factors, DOEConfig(method="full_factorial", levels={f"x{index}": [0, 1] for index in range(10)}))
    with pytest.raises(ValueError):
        generate_doe(_factors(), DOEConfig(method="full_factorial", levels={"x": [2, 3], "y": [0, 1]}))
