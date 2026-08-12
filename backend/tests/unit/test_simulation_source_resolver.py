import pytest

from app.core.repository import LocalSQLiteRepository, SimulationResultRecord
from app.module2_simulation import source_resolution
from app.module2_simulation.source_resolution import (
    SimulationSourceIntegrityError,
    SimulationSourceNotFoundError,
    resolve_simulation_source,
)


@pytest.fixture
def repository(tmp_path, monkeypatch):
    repo = LocalSQLiteRepository(tmp_path / "resolver.db")
    monkeypatch.setattr(source_resolution, "get_repository", lambda: repo)
    return repo


def _simulation(repository, *, owner="owner-a", status="completed", result=True, metrics=None):
    simulation_id = repository.create_simulation_job(
        owner, "thermal_conduction_v1", experiment_id="exp-a", design_id="design-a"
    )
    repository.update_simulation_job(simulation_id, status=status)
    if result:
        repository.record_simulation_result(SimulationResultRecord(
            simulation_id=simulation_id,
            solver_id="thermal_conduction_v1",
            solver_version="1.0.0",
            status="completed",
            summary_metrics=metrics or {"temperature_c": 50.0},
        ))
    return simulation_id


def test_valid_same_owner_simulation_resolves(repository):
    simulation_id = _simulation(repository)
    assert resolve_simulation_source(simulation_id, "owner-a").simulation_id == simulation_id


def test_nonexistent_simulation_is_rejected(repository):
    with pytest.raises(SimulationSourceNotFoundError, match="Simulation not found"):
        resolve_simulation_source("missing", "owner-a")


def test_cross_owner_is_rejected_without_information_leakage(repository):
    simulation_id = _simulation(repository)
    with pytest.raises(SimulationSourceNotFoundError, match="Simulation not found"):
        resolve_simulation_source(simulation_id, "owner-b")


def test_solver_identity_is_returned(repository):
    source = resolve_simulation_source(_simulation(repository), "owner-a")
    assert source.solver_id == "thermal_conduction_v1"


def test_solver_version_is_returned(repository):
    source = resolve_simulation_source(_simulation(repository), "owner-a")
    assert source.solver_version == "1.0.0"


def test_experiment_and_design_linkage_is_returned(repository):
    source = resolve_simulation_source(_simulation(repository), "owner-a")
    assert (source.experiment_id, source.design_id) == ("exp-a", "design-a")


def test_completed_result_requirement_succeeds(repository):
    source = resolve_simulation_source(
        _simulation(repository), "owner-a", require_completed_result=True
    )
    assert source.job_status == source.result.status == "completed"


def test_completed_result_requirement_rejects_incomplete_job(repository):
    simulation_id = _simulation(repository, status="running")
    with pytest.raises(SimulationSourceIntegrityError, match="job is not completed"):
        resolve_simulation_source(simulation_id, "owner-a", require_completed_result=True)


def test_missing_persisted_result_is_rejected_when_required(repository):
    simulation_id = _simulation(repository, result=False)
    with pytest.raises(SimulationSourceIntegrityError, match="result is unavailable"):
        resolve_simulation_source(simulation_id, "owner-a", require_completed_result=True)


def test_required_summary_metric_succeeds_when_present(repository):
    source = resolve_simulation_source(
        _simulation(repository), "owner-a", required_summary_metric="temperature_c"
    )
    assert source.result.summary_metrics["temperature_c"] == 50.0


def test_required_summary_metric_is_rejected_when_absent(repository):
    simulation_id = _simulation(repository, metrics={"other": 1.0})
    with pytest.raises(SimulationSourceIntegrityError, match="required metric 'temperature_c'"):
        resolve_simulation_source(simulation_id, "owner-a", required_summary_metric="temperature_c")
