from __future__ import annotations

import numpy as np
import pytest

from app.module2_simulation.schemas import BoundaryConditions, Geometry, MaterialSelection, SimulationCreateRequest
from app.module2_simulation.solvers.acoustic_solver import AcousticDuctSolver
from app.module2_simulation.solvers.base_solver import SolverValidationError

pytestmark = pytest.mark.integration


def _request(frequency: float = 85.75, elements: int = 80, right: str = "pressure_release"):
    return SimulationCreateRequest(
        solver_id="acoustic_duct_1d_v1", material=MaterialSelection(name="air"),
        geometry=Geometry(dimension="1d", length_m=1.0, num_elements=elements),
        boundary_conditions=BoundaryConditions(source_frequency_hz=frequency, source_pressure_pa=1.0,
                                               acoustic_left_boundary="driven", acoustic_right_boundary=right),
    )


def test_pressure_release_duct_matches_analytical_quarter_sine_profile():
    result, fields = AcousticDuctSolver().run_with_fields(_request())
    amplitude = np.asarray(fields[1].values)
    x = np.linspace(0.0, 1.0, amplitude.size)
    expected = np.cos(np.pi * x / 2.0)
    assert result.convergence.converged
    assert np.max(np.abs(amplitude - expected)) < 2e-4
    assert result.summary_metrics["fundamental_resonance_hz"] == pytest.approx(171.5)
    assert [field.variable_name for field in fields] == ["pressure_real", "pressure_amplitude", "pressure_phase"]


def test_acoustic_grid_refuses_underresolved_frequency_and_invalid_boundary():
    with pytest.raises(SolverValidationError, match="too coarse"):
        AcousticDuctSolver().run(_request(frequency=2000.0, elements=4))
    bad = _request()
    bad.boundary_conditions.acoustic_right_boundary = "anechoic"
    with pytest.raises(SolverValidationError, match="right boundary"):
        AcousticDuctSolver().run(bad)
