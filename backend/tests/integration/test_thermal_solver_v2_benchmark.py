"""
Numerical validation for the Phase C2 unified-architecture thermal solver
(`ThermalConductionSolver`, registered as `thermal_conduction_v1`). These
two cases exercise the new 1d finite-difference conduction solve against
closed-form analytical solutions of the same governing equation
(`k * d2T/dx2 = 0` with no volumetric heat source):

1. Dirichlet-Dirichlet: a rod held at two fixed temperatures has an exact
   linear steady-state temperature profile.
2. Neumann(flux)-Dirichlet: a rod with a prescribed heat flux at one end
   and a fixed temperature at the other also has an exact linear profile,
   with slope determined by the flux and conductivity.

Both are real, executed finite-difference solves (`numpy.linalg.solve` on
the assembled tridiagonal-ish system) - not the analytical formula itself -
so agreement with the closed form (to near machine precision, since a
linear profile has zero second-order finite-difference discretization
error) is what validates the implementation.
"""
import pytest

from app.module2_simulation.materials import get_property
from app.module2_simulation.schemas import (
    BoundaryConditions,
    Geometry,
    MaterialSelection,
    SimulationCreateRequest,
)
from app.module2_simulation.solvers.thermal_solver import ThermalConductionSolver

pytestmark = pytest.mark.benchmark


def test_1d_slab_matches_linear_analytical_profile():
    length_m = 1.0
    num_elements = 10
    t_left = 100.0
    t_right = 20.0

    request = SimulationCreateRequest(
        solver_id="thermal_conduction_v1",
        material=MaterialSelection(name="steel"),
        geometry=Geometry(dimension="1d", length_m=length_m, num_elements=num_elements),
        boundary_conditions=BoundaryConditions(
            ambient_temperature_c=t_left,
            prescribed_temperature_c=t_right,
        ),
    )
    result = ThermalConductionSolver().run(request)

    dx = length_m / num_elements
    for i, temperature in enumerate(result.field_values):
        analytical = t_left + (t_right - t_left) * (i * dx) / length_m
        assert temperature == pytest.approx(analytical, abs=1e-8)

    assert result.convergence.converged is True


def test_1d_prescribed_flux_matches_analytical_profile():
    length_m = 2.0
    num_elements = 20
    heat_flux_w_m2 = 500.0
    t_right = 20.0
    conductivity_w_mk = get_property("steel", "thermal_conductivity").value

    request = SimulationCreateRequest(
        solver_id="thermal_conduction_v1",
        material=MaterialSelection(name="steel"),
        geometry=Geometry(dimension="1d", length_m=length_m, num_elements=num_elements),
        boundary_conditions=BoundaryConditions(
            heat_flux_w_m2=heat_flux_w_m2,
            prescribed_temperature_c=t_right,
        ),
    )
    result = ThermalConductionSolver().run(request)

    dx = length_m / num_elements
    for i, temperature in enumerate(result.field_values):
        x = i * dx
        analytical = t_right + (heat_flux_w_m2 / conductivity_w_mk) * (length_m - x)
        assert temperature == pytest.approx(analytical, abs=1e-6)

    assert result.convergence.converged is True
