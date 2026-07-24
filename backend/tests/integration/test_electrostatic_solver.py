from __future__ import annotations

import numpy as np
import pytest

from app.module2_simulation.schemas import BoundaryConditions, Geometry, MaterialSelection, NumericalSettings, SimulationCreateRequest
from app.module2_simulation.solvers.base_solver import SolverValidationError
from app.module2_simulation.solvers.electrostatic_solver import ElectrostaticRectangularSolver

pytestmark = pytest.mark.integration


def _request(grid: int = 21):
    return SimulationCreateRequest(
        solver_id="electrostatic_rectangular_2d_v1", material=MaterialSelection(name="air"),
        geometry=Geometry(dimension="2d", width_m=2.0, height_m=1.0, grid_resolution=grid, grid_resolution_y=grid),
        boundary_conditions=BoundaryConditions(potential_left_v=2.0, potential_gradient_x_v_m=4.0),
        numerical_settings=NumericalSettings(max_iterations=3000, tolerance=1e-8),
    )


def test_linear_potential_matches_parallel_plate_laplace_solution():
    result, fields = ElectrostaticRectangularSolver().run_with_fields(_request())
    potential = np.asarray(fields[0].values)
    ex = np.asarray(fields[1].values)
    expected = 2.0 + 4.0 * np.linspace(0, 2.0, 21)
    assert result.convergence.converged
    assert np.max(np.abs(potential - expected[None, :])) < 2e-5
    assert np.max(np.abs(ex + 4.0)) < 2e-4
    assert np.max(np.abs(np.asarray(fields[2].values))) < 2e-4


def test_electrostatic_grid_refinement_and_invalid_boundaries():
    coarse = ElectrostaticRectangularSolver().run(_request(11)).summary_metrics["max_electric_field_v_m"]
    fine = ElectrostaticRectangularSolver().run(_request(31)).summary_metrics["max_electric_field_v_m"]
    assert coarse == pytest.approx(4.0, abs=2e-4)
    assert fine == pytest.approx(4.0, abs=2e-4)
    bad = _request()
    bad.boundary_conditions.potential_left_v = None
    with pytest.raises(SolverValidationError, match="all four"):
        ElectrostaticRectangularSolver().run(bad)
