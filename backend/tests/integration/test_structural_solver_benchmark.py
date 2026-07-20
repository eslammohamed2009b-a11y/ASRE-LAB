"""
Numerical validation for the Phase C2 unified-architecture structural solver
(`StructuralLinearSolver`, registered as `structural_linear_1d_v1`).

1. Axial bar: a prismatic bar fixed at one end with an axial load P at the
   free end has an exact closed-form tip displacement `P*L/(E*A)` and
   uniform stress `P/A`.
2. Cantilever beam: a prismatic Euler-Bernoulli cantilever with a
   transverse tip load P has an exact closed-form tip deflection
   `P*L^3/(3*E*I)`. Cubic Hermite beam elements are exact for this loading
   case regardless of element count, so the FEA result should match the
   closed form to near machine precision - this is what actually validates
   the matrix assembly and solve, not a hardcoded formula.
"""
import pytest

from app.module2_simulation.materials import get_property
from app.module2_simulation.schemas import (
    BoundaryConditions,
    Geometry,
    MaterialSelection,
    SimulationCreateRequest,
)
from app.module2_simulation.solvers.structural_solver import StructuralLinearSolver

pytestmark = pytest.mark.benchmark


def test_axial_bar_matches_analytical_solution():
    length_m = 2.0
    area_m2 = 0.01
    load_n = 50_000.0
    elastic_modulus_pa = get_property("steel", "elastic_modulus").value

    request = SimulationCreateRequest(
        solver_id="structural_linear_1d_v1",
        material=MaterialSelection(name="steel"),
        geometry=Geometry(dimension="1d", length_m=length_m, cross_section_area_m2=area_m2, num_elements=5),
        boundary_conditions=BoundaryConditions(axial_load_n=load_n),
    )
    result = StructuralLinearSolver().run(request)

    analytical_displacement_m = load_n * length_m / (elastic_modulus_pa * area_m2)
    analytical_stress_pa = load_n / area_m2

    assert result.summary_metrics["max_displacement_m"] == pytest.approx(analytical_displacement_m, rel=1e-6)
    assert result.summary_metrics["max_stress_pa"] == pytest.approx(analytical_stress_pa, rel=1e-6)
    assert result.summary_metrics["reaction_force_n"] == pytest.approx(load_n, rel=1e-6)
    assert result.convergence.converged is True


def test_cantilever_beam_matches_analytical_tip_deflection():
    length_m = 2.0
    area_m2 = 0.01
    moment_of_inertia_m4 = 8e-6
    load_n = 50_000.0
    elastic_modulus_pa = get_property("steel", "elastic_modulus").value

    request = SimulationCreateRequest(
        solver_id="structural_linear_1d_v1",
        material=MaterialSelection(name="steel"),
        geometry=Geometry(
            dimension="1d",
            length_m=length_m,
            cross_section_area_m2=area_m2,
            moment_of_inertia_m4=moment_of_inertia_m4,
            num_elements=8,
        ),
        boundary_conditions=BoundaryConditions(transverse_load_n=load_n),
    )
    result = StructuralLinearSolver().run(request)

    analytical_tip_deflection_m = (
        load_n * length_m**3 / (3 * elastic_modulus_pa * moment_of_inertia_m4)
    )

    assert result.summary_metrics["max_displacement_m"] == pytest.approx(analytical_tip_deflection_m, rel=1e-6)
    assert result.convergence.converged is True
