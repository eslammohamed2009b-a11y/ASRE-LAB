from __future__ import annotations

import numpy as np
import pytest

from app.module2_simulation.schemas import BoundaryConditions, Geometry, MaterialSelection, SimulationCreateRequest
from app.module2_simulation.solvers.base_solver import SolverValidationError
from app.module2_simulation.solvers.channel_flow_solver import LaminarChannelFlowSolver

pytestmark = pytest.mark.integration


def _request(ny: int = 21, gradient: float = -0.01):
    return SimulationCreateRequest(
        solver_id="cfd_laminar_channel_2d_v1", material=MaterialSelection(name="air"),
        geometry=Geometry(dimension="2d", length_m=0.1, height_m=0.01, grid_resolution=9, grid_resolution_y=ny),
        boundary_conditions=BoundaryConditions(pressure_gradient_pa_m=gradient),
    )


def test_channel_flow_matches_plane_poiseuille_and_conserves_mass():
    result, fields = LaminarChannelFlowSolver().run_with_fields(_request())
    u = np.asarray(fields[0].values)[:, 0]
    y = np.linspace(0, 0.01, 21)
    expected = 0.01 * y * (0.01-y) / (2*1.81e-5)
    assert result.convergence.converged
    assert np.max(np.abs(u-expected)) < 1e-12
    assert result.summary_metrics["mass_conservation_residual_s_1"] < 1e-12
    assert result.summary_metrics["mean_velocity_m_s"] == pytest.approx(0.01*0.01**2/(12*1.81e-5), rel=3e-3)


def test_channel_grid_refinement_and_unsupported_regime_rejection():
    exact = 0.01*0.01**2/(8*1.81e-5)
    for ny in (11, 21, 41):
        maximum = LaminarChannelFlowSolver().run(_request(ny)).summary_metrics["maximum_velocity_m_s"]
        assert maximum == pytest.approx(exact, abs=1e-12)
    with pytest.raises(SolverValidationError, match="outside the declared laminar scope"):
        LaminarChannelFlowSolver().run(_request(gradient=-100.0))
    bad = _request(); bad.boundary_conditions.pressure_gradient_pa_m = 1.0
    with pytest.raises(SolverValidationError, match="negative"):
        LaminarChannelFlowSolver().run(bad)
