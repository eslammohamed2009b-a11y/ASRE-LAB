import numpy as np

from app.core.storage import LocalFileStorage
from app.module2_simulation.field_results import save_field_artifact
from app.module2_simulation.provenance import input_fingerprint, result_hash


def _fingerprint(request=None):
    return input_fingerprint(
        solver_id="thermal_conduction_v1", solver_version="1.0.0",
        request=request or {"geometry": {"length_m": 1.0}},
        material_snapshot={"thermal_conductivity_w_mk": 50.0}, design_id="design-a",
    )


def _result(**overrides):
    values = {
        "solver_id": "thermal_conduction_v1", "solver_version": "1.0.0",
        "input_fingerprint_value": _fingerprint(), "converged": True,
        "iteration_count": 1, "metric": 0.0,
        "tolerance": 1e-9,
        "summary_metrics": {"temperature_c": 50.0},
        "validation_metadata": {"validation_status": "validated"},
        "field_checksums": ["temperature:K:abc"],
        "numerical_method": "finite_difference",
    }
    values.update(overrides)
    return result_hash(**values)


def test_same_scientific_result_has_same_final_hash():
    assert _result() == _result()


def test_scientific_input_change_changes_fingerprint_and_result_identity():
    changed = _fingerprint({"geometry": {"length_m": 2.0}})
    assert changed != _fingerprint()
    assert _result(input_fingerprint_value=changed) != _result()


def test_random_linkage_ids_and_deployment_version_do_not_change_input_identity():
    request = {"geometry": {"length_m": 1.0}, "experiment_id": "exp-one", "design_id": "design-one"}
    first = input_fingerprint(
        solver_id="thermal_conduction_v1", solver_version="1.0.0", request=request,
        material_snapshot={"k": 50.0}, design_id="design-one", application_version="one",
    )
    second = input_fingerprint(
        solver_id="thermal_conduction_v1", solver_version="1.0.0",
        request={**request, "experiment_id": "exp-two", "design_id": "design-two"},
        material_snapshot={"k": 50.0}, design_id="design-two", application_version="two",
    )
    assert first == second


def test_summary_metric_change_changes_result_hash():
    assert _result(summary_metrics={"temperature_c": 51.0}) != _result()


def test_convergence_state_change_changes_result_hash():
    assert _result(converged=False, iteration_count=300, metric=0.1) != _result()
    assert _result(tolerance=1e-6) != _result()


def test_field_content_change_changes_checksum_and_final_hash(tmp_path):
    storage = LocalFileStorage(tmp_path / "objects")
    common = dict(storage=storage, user_id="user", experiment_id="exp", simulation_id="sim",
                  variable_name="temperature", unit="K", axes=[{"name": "x", "unit": "m", "values": [0, 1]}])
    first = save_field_artifact(values=np.array([1.0, 2.0]), **common)
    changed = save_field_artifact(values=np.array([1.0, 3.0]), **common)
    assert first.reproducibility_hash != changed.reproducibility_hash
    assert _result(field_checksums=[first.reproducibility_hash]) != _result(
        field_checksums=[changed.reproducibility_hash]
    )


def test_timestamps_elapsed_runtime_and_signed_locations_do_not_change_hash():
    first = _result(validation_metadata={
        "validation_status": "validated", "created_at": "2026-01-01",
        "elapsed_time_seconds": 1.0, "signed_url": "https://one", "temporary_path": "C:/one",
    })
    second = _result(validation_metadata={
        "validation_status": "validated", "created_at": "2027-01-01",
        "elapsed_time_seconds": 900.0, "signed_url": "https://two", "temporary_path": "C:/two",
    })
    assert first == second


def test_field_artifact_order_is_deterministic():
    assert _result(field_checksums=["b", "a", "c"]) == _result(field_checksums=["c", "b", "a"])


def test_same_field_content_has_same_checksum_independent_of_storage_location(tmp_path):
    axes = [{"unit": "m", "name": "x", "values": [0, 1]}]
    first = save_field_artifact(
        storage=LocalFileStorage(tmp_path / "one"), user_id="user-a", experiment_id="exp-a",
        simulation_id="sim-a", variable_name="temperature", unit="K", axes=axes,
        values=np.array([1.0, 2.0]),
    )
    second = save_field_artifact(
        storage=LocalFileStorage(tmp_path / "two"), user_id="user-b", experiment_id="exp-b",
        simulation_id="sim-b", variable_name="temperature", unit="K",
        axes=[{"values": [0, 1], "unit": "m", "name": "x"}],
        values=np.array([1.0, 2.0]),
    )
    assert first.checksum_sha256 == second.checksum_sha256
    assert first.reproducibility_hash == second.reproducibility_hash


def test_changed_field_content_changes_binary_and_scientific_checksums(tmp_path):
    storage = LocalFileStorage(tmp_path / "objects")
    values = dict(storage=storage, user_id="u", experiment_id="e", simulation_id="s",
                  variable_name="pressure", unit="Pa", axes=[{"name": "x", "unit": "m", "values": [0, 1]}])
    first = save_field_artifact(values=np.array([1.0, 2.0]), **values)
    second = save_field_artifact(values=np.array([2.0, 1.0]), **values)
    assert first.checksum_sha256 != second.checksum_sha256
    assert first.reproducibility_hash != second.reproducibility_hash
