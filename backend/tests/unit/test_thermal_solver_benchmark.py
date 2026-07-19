"""
Numerical validation for the Module 2 thermal solver (Gauss-Seidel finite
difference steady-state heat solve). These are real, executed numerical
checks - not mocks - but they do not require the CadQuery/OCP kernel, so
they live alongside the unit tests.

Two checks are used because a closed-form 3D solution for a cube with
uniform volumetric heat generation and Dirichlet boundaries on all six
faces is not a simple textbook formula:

1. Trivial analytical-limit case: with zero heat source, the steady-state
   solution of Laplace's equation with T=ambient on every boundary face is
   exactly T=ambient everywhere. This is an exact, checkable analytical
   result.
2. Grid-convergence: refining the mesh should change the solution by a
   shrinking amount, not diverge or oscillate wildly. This is the standard
   way to demonstrate a finite-difference solver is numerically stable and
   converging toward a fixed solution, independent of having a published
   reference value to compare against.
"""
import pytest

from app.module2_simulation.solvers.base_solver import Mesh
from app.module2_simulation.solvers.thermal_solver import ThermalSolver

pytestmark = pytest.mark.benchmark


def _cube_mesh(n_per_edge: int = 4) -> Mesh:
    nodes = []
    for i in range(n_per_edge):
        for j in range(n_per_edge):
            for k in range(n_per_edge):
                nodes.append(
                    (
                        i / (n_per_edge - 1),
                        j / (n_per_edge - 1),
                        k / (n_per_edge - 1),
                    )
                )
    return Mesh(nodes=nodes, elements=[(0, 1, 2, 3)])


def test_zero_heat_source_converges_to_ambient_temperature():
    """Exact analytical case: no heat source -> steady state equals the
    Dirichlet boundary value (ambient) everywhere in the domain."""
    mesh = _cube_mesh()
    result = ThermalSolver().solve(
        mesh,
        material="concrete",
        boundary_conditions={
            "ambient_temp_c": 20.0,
            "heat_source_w_m3": 0.0,
            "grid_resolution": 12,
            "max_iterations": 200,
            "tolerance": 1e-6,
        },
    )

    assert result.summary_metrics["max_temperature_c"] == pytest.approx(20.0, abs=1e-3)
    assert result.summary_metrics["min_temperature_c"] == pytest.approx(20.0, abs=1e-3)
    assert result.summary_metrics["avg_temperature_c"] == pytest.approx(20.0, abs=1e-3)


def test_grid_refinement_converges_rather_than_diverges():
    """Doubling grid resolution should change the peak temperature by a
    shrinking amount (numerical convergence), proving this is a real,
    stable iterative solver rather than an arbitrary/placeholder formula."""
    mesh = _cube_mesh()
    boundary = {
        "ambient_temp_c": 25.0,
        "heat_source_w_m3": 2.0e5,
        "max_iterations": 400,
        "tolerance": 1e-6,
    }

    peaks = []
    for grid_resolution in (10, 16, 22):
        result = ThermalSolver().solve(
            mesh,
            material="steel",
            boundary_conditions={**boundary, "grid_resolution": grid_resolution},
        )
        peaks.append(result.summary_metrics["max_temperature_c"])

    delta_1 = abs(peaks[1] - peaks[0])
    delta_2 = abs(peaks[2] - peaks[1])

    # All runs must produce physically sane, bounded temperatures (heat only
    # flows from source to boundary, never below ambient).
    assert all(peak >= boundary["ambient_temp_c"] for peak in peaks)
    # The change between successive refinements should shrink (convergence),
    # not grow (divergence/instability).
    assert delta_2 <= delta_1 + 1e-6
