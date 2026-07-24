from __future__ import annotations

import inspect

import pytest

from app import pipeline_service
from app.core.repository import LocalSQLiteRepository, SupabaseRepository
from app.core.storage import LocalFileStorage
from app.module2_simulation.schemas import AnalysisType

pytestmark = pytest.mark.unit


def _design(design_id: str) -> dict:
    return {
        "design_id": design_id,
        "params": {
            "geometry_type": "tower",
            "base_length_m": 20.0,
            "height_m": 100.0,
            "slope_angle_deg": 0.0,
            "material": "concrete",
            "wall_thickness_m": 0.5,
        },
    }


def _configure_pipeline(monkeypatch, db_path, designs=None):
    monkeypatch.setattr(
        pipeline_service, "get_repository", lambda: LocalSQLiteRepository(db_path)
    )
    monkeypatch.setattr(
        pipeline_service,
        "generate_design_matrix",
        lambda request: designs if designs is not None else [_design("design-1")],
    )
    monkeypatch.setattr(
        pipeline_service, "get_storage", lambda: LocalFileStorage(db_path.parent / "objects")
    )
    monkeypatch.setattr(
        pipeline_service,
        "persist_generated_design",
        lambda **kwargs: kwargs["repo"].create_design_model(
            kwargs["experiment_id"], kwargs["user_id"],
            kwargs["params"].geometry_type.value, kwargs["result"]["params"],
            {"length": "m", "angle": "deg"}, kwargs["variation_index"],
        ),
    )


def test_pipeline_creation_and_retrieval_use_authoritative_contract(monkeypatch, tmp_path):
    db_path = tmp_path / "pipeline.sqlite3"
    _configure_pipeline(monkeypatch, db_path)

    job_id, experiment_id = pipeline_service.create_pipeline_job(
        "tower 100 m", 1, [AnalysisType.THERMAL], "owner-a"
    )
    created = LocalSQLiteRepository(db_path)
    experiment = created.get_experiment(experiment_id)
    job = pipeline_service.get_pipeline_job_service(job_id, "owner-a")

    assert experiment.user_id == "owner-a"
    assert experiment.name == "Integrated pipeline run"
    assert experiment.input_specification["prompt"] == "tower 100 m"
    assert job["status"] == "queued"
    assert job["experiment_id"] == experiment_id


def test_pipeline_retrieval_denies_other_owner(monkeypatch, tmp_path):
    db_path = tmp_path / "pipeline.sqlite3"
    _configure_pipeline(monkeypatch, db_path)
    job_id, _ = pipeline_service.create_pipeline_job(
        "tower 100 m", 1, [AnalysisType.THERMAL], "owner-a"
    )

    with pytest.raises(pipeline_service.PipelineNotFoundError):
        pipeline_service.get_pipeline_job_service(job_id, "owner-b")


def test_partial_failure_preserves_successful_design_and_simulation(monkeypatch, tmp_path):
    db_path = tmp_path / "pipeline.sqlite3"
    calls = 0

    original_run = pipeline_service.run_simulation_job

    def simulate(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            kwargs["repository"].update_simulation_job(
                kwargs["simulation_id"], status="failed", progress_percent=100,
                error_code="test_failure", safe_error_message="Injected failure",
            )
            return {"simulation_id": kwargs["simulation_id"], "status": "failed"}
        return original_run(**kwargs)

    _configure_pipeline(
        monkeypatch, db_path, designs=[_design("design-1"), _design("design-2")]
    )
    monkeypatch.setattr(pipeline_service, "run_simulation_job", simulate)
    result = pipeline_service.run_pipeline_flow(
        "tower 100 m", 2, [AnalysisType.THERMAL], "owner-a"
    )

    reloaded = LocalSQLiteRepository(db_path)
    job = reloaded.get_job(result["job_id"])
    designs = reloaded.list_design_models_for_experiment(result["experiment_id"])
    assert job.status == "partial_failure"
    assert job.completed_count == 1
    assert job.failed_count == 1
    assert len(designs) == 2
    successful_results = []
    for design in designs:
        # The simulation id is intentionally independent of the legacy CAD id;
        # inspect SQLite only to prove one immutable result survived the peer failure.
        with reloaded._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM simulation_jobs WHERE design_id = ?", (design.id,)
            ).fetchall()
        successful_results.extend(
            result for row in rows if (result := reloaded.get_simulation_result(row["id"])) is not None
        )
    assert len(successful_results) == 1
    assert successful_results[0].solver_id == "thermal_conduction_v1"
    assert successful_results[0].numerical_method
    assert len(reloaded.list_field_results(successful_results[0].simulation_id)) == 1


def test_pipeline_cancellation_is_owner_only_and_durable(monkeypatch, tmp_path):
    db_path = tmp_path / "pipeline.sqlite3"
    _configure_pipeline(monkeypatch, db_path)
    job_id, _ = pipeline_service.create_pipeline_job(
        "tower 100 m", 1, [AnalysisType.THERMAL], "owner-a"
    )

    cancelled = pipeline_service.cancel_pipeline_job_service(job_id, "owner-a")
    assert cancelled["status"] == "cancelled"
    assert LocalSQLiteRepository(db_path).get_job(job_id).status == "cancelled"
    with pytest.raises(pipeline_service.PipelineNotFoundError):
        pipeline_service.cancel_pipeline_job_service(job_id, "owner-b")


def test_completed_pipeline_state_survives_repository_reload(monkeypatch, tmp_path):
    db_path = tmp_path / "pipeline.sqlite3"
    _configure_pipeline(monkeypatch, db_path)
    result = pipeline_service.run_pipeline_flow(
        "tower 100 m", 1, [AnalysisType.THERMAL], "owner-a"
    )

    assert result["status"] == "completed"
    assert result["analysis_id"]
    assert result["analysis"]["analysis_type"] == "engineering_intelligence"
    assert "not inferred service conditions" in result["reference_scenarios"]["thermal"]
    reloaded = LocalSQLiteRepository(db_path).get_job(result["job_id"])
    assert reloaded.status == "completed"
    assert reloaded.progress_percent == 100


class _Query:
    def __init__(self, client, table):
        self.client = client
        self.table = table
        self.payload = None

    def insert(self, payload):
        self.payload = payload
        self.client.inserts.append((self.table, payload))
        return self

    def execute(self):
        identifier = "exp-1" if self.table == "experiments" else "job-1"
        return type("Response", (), {"data": [{"id": identifier}]})()


class _RecordingClient:
    def __init__(self):
        self.tables = []
        self.inserts = []

    def table(self, name):
        self.tables.append(name)
        return _Query(self, name)


def test_pipeline_never_uses_stale_columns_or_simulation_metrics():
    client = _RecordingClient()
    repo = SupabaseRepository(client)
    pipeline_service.create_pipeline_job(
        "tower 100 m", 1, [AnalysisType.THERMAL], "owner-a", repo=repo
    )

    experiment_payload = client.inserts[0][1]
    assert set(experiment_payload) >= {"user_id", "name", "input_specification"}
    assert not {"owner_id", "title", "description"} & set(experiment_payload)
    assert "simulation_metrics" not in client.tables
    source = inspect.getsource(pipeline_service)
    assert "app.core.persistence" not in source
    assert "simulation_metrics" not in source
    assert "run_simulation_service" not in source
    assert "cluster_designs" not in source
    assert "synthesize_report" not in source


def test_pipeline_rejects_unsupported_family_without_empirical_fallback(monkeypatch, tmp_path):
    db_path = tmp_path / "pipeline.sqlite3"
    _configure_pipeline(monkeypatch, db_path)

    result = pipeline_service.run_pipeline_flow(
        "tower 100 m", 1, [AnalysisType.WIND_LOAD], "owner-a"
    )

    assert result["status"] == "failed"
    assert result["skipped_analyses"] == ["wind_load"]
    assert result["analysis_id"] is None
    repo = LocalSQLiteRepository(db_path)
    assert repo.list_simulation_jobs_for_experiment(result["experiment_id"]) == []
