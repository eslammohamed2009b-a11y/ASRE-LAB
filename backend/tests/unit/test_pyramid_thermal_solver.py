import numpy as np

from app.module2_simulation.schemas import SimulationCreateRequest
from app.module2_simulation.solvers.pyramid_thermal_solver import PyramidThermalConductionSolver, solve_pyramid_conduction


def solve(**changes):
    inputs = {
        "base_length_m": 2.0,
        "height_m": 2.0,
        "grid_resolution": 17,
        "conductivity_w_mk": 1.7,
        "ambient_temperature_c": 20.0,
        "base_temperature_c": 20.0,
        "heat_source_w_m3": 1000.0,
        "max_iterations": 2000,
        "tolerance": 1e-8,
    }
    inputs.update(changes)
    return solve_pyramid_conduction(**inputs)


def test_zero_source_equal_boundaries_matches_constant_analytical_solution():
    result = solve(heat_source_w_m3=0.0, base_temperature_c=25.0, ambient_temperature_c=25.0)
    assert np.allclose(result["active_values"], 25.0, atol=1e-12)
    assert result["residual"] <= result["tolerance"]


def test_geometry_change_changes_geometry_sensitive_result():
    short = solve(height_m=1.0)
    tall = solve(height_m=4.0)
    assert not np.isclose(short["active_values"].max(), tall["active_values"].max())
    assert short["estimated_domain_volume_m3"] != tall["estimated_domain_volume_m3"]
    assert short["integrated_heat_source_w"] != tall["integrated_heat_source_w"]


def test_resolution_convergence_is_reported():
    coarse = solve(grid_resolution=9)
    fine = solve(grid_resolution=17)
    analytical_volume = 2.0 * 2.0 * 2.0 / 3.0
    assert abs(fine["estimated_domain_volume_m3"] - analytical_volume) < abs(
        coarse["estimated_domain_volume_m3"] - analytical_volume
    )
    assert fine["residual_history"]
    assert fine["residual_history"][-1] <= fine["residual_history"][0]
    assert fine["residual"] <= fine["tolerance"]


def test_solver_result_carries_benchmark_and_masked_field_provenance():
    request = SimulationCreateRequest(
        solver_id="pyramid_thermal_conduction_v1",
        design_id="design-1",
        material={"name": "concrete"},
        geometry={"dimension": "pyramid3d", "base_length_m": 2, "height_m": 2, "grid_resolution": 9},
        boundary_conditions={
            "ambient_temperature_c": 20, "prescribed_temperature_c": 20, "heat_source_w_m3": 100,
        },
        numerical_settings={"max_iterations": 1000, "tolerance": 1e-6},
    )
    result, fields = PyramidThermalConductionSolver().run_with_fields(request)
    assert result.validation_metadata["benchmark"]["passed"] is True
    assert result.validation_metadata["convergence_evidence"]["resolution_refinement_performed_for_current_run"] is False
    assert {field.variable_name for field in fields} == {"temperature", "pyramid_domain_mask"}
    assert result.source_design_id == "design-1"
