from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.repository import AnalysisRecord, LocalSQLiteRepository, SimulationResultRecord, SupabaseRepository
from app.module3_analysis.dataset import DatasetBuildError, build_experiment_dataset
from app.module3_analysis import dataset as dataset_module
from app.module3_analysis.intelligence import (
    AnalysisInputError,
    correlations,
    descriptive_statistics,
    grounded_recommendations,
    pareto_front,
    regression_sensitivity,
    weighted_ranking,
)
from app.module3_analysis.schemas import ObjectiveSpec, SensitivitySpec
from app.module3_analysis.schemas import AnalysisCreateRequest
from app.module3_analysis.service import (
    AnalysisNotFoundError,
    get_analysis_for_user,
    list_analyses_for_user,
    run_experiment_analysis,
)


def _seed(repo: LocalSQLiteRepository, *, owner: str = "user-a", count: int = 8):
    experiment_id = repo.create_experiment(owner, "intelligence", {"objective": "trade-off"})
    simulation_ids = []
    for index in range(1, count + 1):
        design_id = repo.create_design_model(
            experiment_id, owner, "beam", {"width": float(index), "label": f"d{index}"},
            {"width": "m"}, index,
        )
        simulation_id = repo.create_simulation_job(
            owner, "structural_linear_static", experiment_id, design_id,
        )
        repo.record_simulation_input(
            simulation_id, "steel", {"density": 7800.0 + index}, {"density": "kg/m^3"},
            {}, {"axial_load_n": 100.0}, {"tolerance": 1e-6},
        )
        repo.record_simulation_result(SimulationResultRecord(
            simulation_id=simulation_id, solver_id="structural_linear_static", solver_version="1.0",
            converged=True, residual=1e-8, iteration_count=1, tolerance=1e-6,
            summary_metrics={"strength_pa": float(index * 2), "mass_kg": float(10 - index)},
        ))
        repo.update_simulation_job(simulation_id, status="completed", progress_percent=100)
        simulation_ids.append(simulation_id)
    return experiment_id, simulation_ids


def test_dataset_is_deterministic_evidence_linked_and_owner_scoped(tmp_path):
    repo = LocalSQLiteRepository(tmp_path / "analysis.db")
    experiment_id, simulation_ids = _seed(repo)

    first = build_experiment_dataset(repo, experiment_id, "user-a")
    second = build_experiment_dataset(LocalSQLiteRepository(tmp_path / "analysis.db"), experiment_id, "user-a")

    assert first.dataset_hash == second.dataset_hash
    assert first.quality.valid_row_count == 8
    assert first.units["design.width"] == "m"
    assert first.units["metric.strength_pa"] == "Pa"
    assert first.rows[0].simulation_id in simulation_ids
    assert first.rows[0].evidence_ids
    assert "design.label" in first.quality.non_numeric_fields
    with pytest.raises(DatasetBuildError, match="Experiment not found"):
        build_experiment_dataset(repo, experiment_id, "user-b")


def test_statistics_correlations_and_sensitivity_are_deterministic(tmp_path):
    repo = LocalSQLiteRepository(tmp_path / "analysis.db")
    experiment_id, _ = _seed(repo)
    dataset = build_experiment_dataset(repo, experiment_id, "user-a")

    stats = descriptive_statistics(dataset)
    relation = correlations(dataset, "both")["relationships"][0]
    sensitivity = regression_sensitivity(dataset, SensitivitySpec(
        target="metric.strength_pa", features=["design.width"],
    ))

    assert stats["design.width"]["mean"] == pytest.approx(4.5)
    assert abs(relation["pearson"]["coefficient"]) == pytest.approx(1.0)
    assert relation["effect_size_interpretation"] == "very_large"
    assert "does not establish causation" in relation["warning"]
    assert sensitivity["features"][0]["standardized_coefficient"] == pytest.approx(1.0)
    assert sensitivity["r_squared"] == pytest.approx(1.0)
    assert len(sensitivity["evidence_simulation_ids"]) == 8


def test_sensitivity_rejects_small_or_invalid_datasets(tmp_path):
    repo = LocalSQLiteRepository(tmp_path / "analysis.db")
    experiment_id, _ = _seed(repo, count=3)
    dataset = build_experiment_dataset(repo, experiment_id, "user-a")
    with pytest.raises(AnalysisInputError, match="at least"):
        regression_sensitivity(dataset, SensitivitySpec(
            target="metric.strength_pa", features=["design.width"],
        ))
    with pytest.raises(AnalysisInputError, match="Unknown"):
        regression_sensitivity(dataset, SensitivitySpec(
            target="metric.unknown", features=["design.width"],
        ))


def test_pareto_ranking_and_recommendation_reference_real_evidence(tmp_path):
    repo = LocalSQLiteRepository(tmp_path / "analysis.db")
    experiment_id, _ = _seed(repo)
    dataset = build_experiment_dataset(repo, experiment_id, "user-a")
    objectives = [
        ObjectiveSpec(column="metric.strength_pa", direction="maximize", weight=2),
        ObjectiveSpec(column="metric.mass_kg", direction="minimize", weight=1),
    ]

    pareto = pareto_front(dataset, objectives)
    ranking = weighted_ranking(dataset, objectives)
    recommendation = grounded_recommendations(ranking, pareto)[0]

    assert len(pareto["pareto_optimal"]) == 1
    assert len(pareto["dominated"]) == 7
    assert ranking["ranking"][0]["objective_values"]["metric.strength_pa"] == 16.0
    assert recommendation["evidence"]["source_ids"]
    assert "user-supplied" in recommendation["warnings"][0]


def test_analysis_is_persisted_across_restart_and_owner_scoped(tmp_path):
    db_path = tmp_path / "analysis.db"
    repo = LocalSQLiteRepository(db_path)
    experiment_id, _ = _seed(repo)
    response = run_experiment_analysis(
        experiment_id,
        "user-a",
        AnalysisCreateRequest(objectives=[
            ObjectiveSpec(column="metric.strength_pa", direction="maximize"),
            ObjectiveSpec(column="metric.mass_kg", direction="minimize"),
        ]),
        repo,
    )

    restarted = LocalSQLiteRepository(db_path)
    stored = get_analysis_for_user(response.id, "user-a", restarted)
    listed = list_analyses_for_user(experiment_id, "user-a", restarted)
    assert stored.dataset_hash == response.dataset_hash
    assert stored.result["recommendations"][0]["evidence"]["source_ids"]
    assert [item.id for item in listed] == [response.id]
    with pytest.raises(AnalysisNotFoundError):
        get_analysis_for_user(response.id, "user-b", restarted)
    with pytest.raises(AnalysisNotFoundError):
        list_analyses_for_user(experiment_id, "user-b", restarted)


def test_dataset_quality_detects_missing_units_constants_duplicates_and_job_states(tmp_path, monkeypatch):
    repo = LocalSQLiteRepository(tmp_path / "quality.db")
    experiment_id = repo.create_experiment("user-a", "quality")
    jobs = []
    for index, (status, unit) in enumerate([
        ("completed", "m"), ("partial_failure", "mm"), ("failed", "m"),
    ]):
        parameters = {"width": float(index + 1), "constant": 7.0}
        if index == 1:
            parameters["optional"] = 3.0
        design_id = repo.create_design_model(
            experiment_id, "user-a", "beam", parameters,
            {"width": unit, "constant": "m", "optional": "m"}, index,
        )
        sim_id = repo.create_simulation_job("user-a", "solver", experiment_id, design_id)
        repo.record_simulation_input(sim_id, "steel", {"density": 1.0}, {"density": "kg/m^3"}, {}, {}, {})
        repo.record_simulation_result(SimulationResultRecord(
            simulation_id=sim_id, solver_id="solver", solver_version="1", converged=True,
            summary_metrics={"score": float(index)},
        ))
        repo.update_simulation_job(sim_id, status=status)
        jobs.append(repo.get_simulation_job(sim_id))

    original = repo.list_simulation_jobs_for_experiment
    monkeypatch.setattr(repo, "list_simulation_jobs_for_experiment", lambda _: original(experiment_id) + [jobs[0]])
    dataset = build_experiment_dataset(repo, experiment_id, "user-a")
    assert dataset.quality.valid_row_count == 2
    assert dataset.quality.excluded_row_count == 2
    assert dataset.quality.duplicate_simulation_ids == [jobs[0].id]
    assert dataset.quality.missing_value_counts["design.optional"] == 1
    assert "design.constant" in dataset.quality.constant_columns
    assert dataset.quality.incompatible_units["design.width"] == ["m", "mm"]


def test_dataset_row_limit_fails_closed(tmp_path, monkeypatch):
    repo = LocalSQLiteRepository(tmp_path / "limit.db")
    experiment_id, _ = _seed(repo, count=1)
    job = repo.list_simulation_jobs_for_experiment(experiment_id)[0]
    monkeypatch.setattr(
        repo, "list_simulation_jobs_for_experiment",
        lambda _: [job] * (dataset_module.MAX_ROWS + 1),
    )
    with pytest.raises(DatasetBuildError, match="simulation analysis limit"):
        build_experiment_dataset(repo, experiment_id, "user-a")


def test_ranking_constant_objective_and_duplicate_pareto_points_are_deterministic(tmp_path):
    repo = LocalSQLiteRepository(tmp_path / "ties.db")
    experiment_id = repo.create_experiment("user-a", "ties")
    for index, (strength, mass) in enumerate([(10.0, 5.0), (10.0, 5.0), (8.0, 7.0)]):
        design = repo.create_design_model(
            experiment_id, "user-a", "beam", {"width": float(index + 1)}, {"width": "m"}, index,
        )
        sim = repo.create_simulation_job("user-a", "solver", experiment_id, design)
        repo.record_simulation_input(sim, "steel", {"density": 7800.0}, {"density": "kg/m^3"}, {}, {}, {})
        repo.record_simulation_result(SimulationResultRecord(
            simulation_id=sim, solver_id="solver", solver_version="1", converged=True,
            summary_metrics={"strength_pa": strength, "mass_kg": mass, "constant": 1.0},
        ))
        repo.update_simulation_job(sim, status="completed")
    dataset = build_experiment_dataset(repo, experiment_id, "user-a")
    objectives = [
        ObjectiveSpec(column="metric.strength_pa", direction="maximize"),
        ObjectiveSpec(column="metric.mass_kg", direction="minimize"),
        ObjectiveSpec(column="metric.constant", direction="maximize"),
    ]
    pareto = pareto_front(dataset, objectives)
    ranking = weighted_ranking(dataset, objectives)
    assert len(pareto["pareto_optimal"]) == 2
    assert len(pareto["dominated"]) == 1
    assert any("constant" in warning for warning in ranking["warnings"])
    assert ranking["ranking"][0]["score"] == pytest.approx(
        sum(ranking["ranking"][0]["contributions"].values())
    )


def test_supabase_analysis_adapter_uses_authoritative_table_and_fields():
    class Query:
        def __init__(self, client, table):
            self.client, self.table = client, table

        def insert(self, payload):
            self.client.insert = (self.table, payload)
            return self

        def execute(self):
            return type("Response", (), {"data": []})()

    class Client:
        insert = None

        def table(self, name):
            return Query(self, name)

    client = Client()
    SupabaseRepository(client).create_analysis(AnalysisRecord(
        id="analysis-1", experiment_id="experiment-1", user_id="user-1",
        analysis_type="engineering_intelligence", status="completed",
        dataset_hash="a" * 64, reproducibility_hash="b" * 64,
        source_design_ids=["design-1"], source_simulation_ids=["simulation-1"],
    ))
    table, payload = client.insert
    assert table == "experiment_analyses"
    assert payload["user_id"] == "user-1"
    assert payload["source_simulation_ids"] == ["simulation-1"]
    assert payload["reproducibility_hash"] == "b" * 64


def test_objective_weights_are_validated_and_recommendations_never_claim_causation(tmp_path):
    with pytest.raises(ValidationError):
        ObjectiveSpec(column="metric.score", direction="maximize", weight=0)
    repo = LocalSQLiteRepository(tmp_path / "language.db")
    experiment_id, _ = _seed(repo)
    result = run_experiment_analysis(
        experiment_id, "user-a",
        AnalysisCreateRequest(
            sensitivity={"target": "metric.strength_pa", "features": ["design.width"]},
            objectives=[
                {"column": "metric.strength_pa", "direction": "maximize"},
                {"column": "metric.mass_kg", "direction": "minimize"},
            ],
        ),
        repo,
    )
    statements = " ".join(item["statement"].lower() for item in result.result["recommendations"])
    assert " causes " not in statements
    assert all(item["evidence"] for item in result.result["recommendations"])
