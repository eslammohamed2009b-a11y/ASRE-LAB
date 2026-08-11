from app.module2_simulation.solvers.thermal_solver import _solve_steady_state_heat


def test_3d_thermal_reports_actual_convergence_evidence():
    field, evidence = _solve_steady_state_heat(5, 20.0, 1e4, 1.7, 300, 1e-5)
    assert field.shape == (5, 5, 5)
    assert evidence["converged"] is True
    assert evidence["iterations"] <= 300
    assert evidence["final_max_delta"] < evidence["tolerance"]


def test_3d_thermal_reports_iteration_limit_as_nonconverged():
    _, evidence = _solve_steady_state_heat(10, 20.0, 2e5, 1.7, 1, 1e-20)
    assert evidence["iterations"] == 1
    assert evidence["tolerance"] == 1e-20
    assert evidence["converged"] is False
    assert evidence["final_max_delta"] >= evidence["tolerance"]
