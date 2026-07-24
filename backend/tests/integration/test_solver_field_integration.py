import pytest

from app.core.repository import LocalSQLiteRepository
from app.core.storage import LocalFileStorage
from app.module2_simulation import tasks

pytestmark = pytest.mark.integration


def _run(tmp_path, monkeypatch, solver_id, geometry, boundary_conditions, material="steel"):
    repo = LocalSQLiteRepository(tmp_path / f"{solver_id}.db")
    storage = LocalFileStorage(tmp_path / f"{solver_id}-objects")
    simulation_id = repo.create_simulation_job(
        user_id="user-a", solver_id=solver_id, experiment_id="experiment-a", design_id="design-a"
    )
    monkeypatch.setattr(tasks, "get_repository", lambda: repo)
    monkeypatch.setattr(tasks, "get_storage", lambda: storage)
    result = tasks.run_simulation_job(
        simulation_id=simulation_id, solver_id=solver_id, material_name=material,
        geometry=geometry, boundary_conditions=boundary_conditions,
        initial_conditions={}, numerical_settings={"max_iterations": 300, "tolerance": 1e-5},
        experiment_id="experiment-a", design_id="design-a",
    )
    assert result["status"] == "completed"
    return repo, storage, simulation_id


def test_thermal_persists_genuine_temperature_field(tmp_path, monkeypatch):
    repo, storage, simulation_id = _run(
        tmp_path, monkeypatch, "thermal_conduction_v1",
        {"dimension": "1d", "length_m": 1.0, "num_elements": 10},
        {"ambient_temperature_c": 100.0, "prescribed_temperature_c": 20.0},
    )
    fields = repo.list_field_results(simulation_id)
    assert [(f.variable_name, f.unit, f.array_shape) for f in fields] == [("temperature", "degC", [11])]
    assert storage.file_exists(fields[0].storage_object_key)
    assert "convergence" in fields[0].grid_metadata
    assert "warnings" in fields[0].grid_metadata
    persisted = repo.get_simulation_result(simulation_id)
    assert persisted.numerical_method
    assert len(persisted.reproducibility_hash) == 64
    assert persisted.source_design_id == "design-a"


def test_structural_persists_displacement_and_real_element_stress(tmp_path, monkeypatch):
    repo, _, simulation_id = _run(
        tmp_path, monkeypatch, "structural_linear_1d_v1",
        {"dimension": "1d", "length_m": 1.0, "cross_section_area_m2": 0.01, "num_elements": 4},
        {"axial_load_n": 1000.0},
    )
    fields = repo.list_field_results(simulation_id)
    assert [(f.variable_name, f.unit) for f in fields] == [
        ("axial_displacement", "m"), ("axial_stress", "Pa")
    ]


def test_modal_beam_persists_modes_but_sdof_remains_scalar_only(tmp_path, monkeypatch):
    repo, _, simulation_id = _run(
        tmp_path, monkeypatch, "modal_eigen_1d_v1",
        {"dimension": "1d", "length_m": 1.0, "cross_section_area_m2": 0.01,
         "moment_of_inertia_m4": 1e-6, "num_elements": 4}, {},
    )
    fields = repo.list_field_results(simulation_id)
    assert len(fields) == 1
    assert fields[0].variable_name == "mode_shape"
    assert fields[0].dimensions == 2

    repo2, _, simulation_id2 = _run(
        tmp_path, monkeypatch, "modal_eigen_1d_v1",
        {"dimension": "1d"}, {"point_mass_kg": 2.0, "spring_stiffness_n_m": 200.0},
    )
    assert repo2.list_field_results(simulation_id2) == []
    assert repo2.get_simulation_result(simulation_id2).summary_metrics["fundamental_frequency_hz"] > 0


@pytest.mark.parametrize(("solver_id","material","geometry","boundary","names"), [
    ("acoustic_duct_1d_v1","air",{"dimension":"1d","length_m":1,"num_elements":20},
     {"source_frequency_hz":80,"source_pressure_pa":1,"acoustic_left_boundary":"driven","acoustic_right_boundary":"pressure_release"},
     ["pressure_amplitude","pressure_phase","pressure_real"]),
    ("electrostatic_rectangular_2d_v1","air",{"dimension":"2d","width_m":1,"height_m":1,"grid_resolution":9,"grid_resolution_y":9},
     {"potential_left_v":0,"potential_gradient_x_v_m":1},
     ["electric_field_magnitude","electric_field_x","electric_field_y","electric_potential"]),
    ("cfd_laminar_channel_2d_v1","air",{"dimension":"2d","length_m":.1,"height_m":.01,"grid_resolution":9,"grid_resolution_y":11},
     {"pressure_gradient_pa_m":-.01},["pressure","velocity_magnitude","velocity_x","velocity_y"]),
])
def test_new_solver_fields_use_authoritative_persistence_and_owner_metadata(tmp_path,monkeypatch,solver_id,material,geometry,boundary,names):
    repo,storage,simulation_id=_run(tmp_path,monkeypatch,solver_id,geometry,boundary,material)
    fields=repo.list_field_results(simulation_id)
    assert [field.variable_name for field in fields]==names
    assert all(field.user_id=="user-a" and storage.file_exists(field.storage_object_key) for field in fields)
    assert all("convergence" in field.grid_metadata for field in fields)
