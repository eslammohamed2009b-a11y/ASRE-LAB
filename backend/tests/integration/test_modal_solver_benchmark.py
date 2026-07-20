"""
Numerical validation for the Phase C2 unified-architecture modal solver
(`ModalSolver`, registered as `modal_eigen_1d_v1`).

1. SDOF mass-spring: `omega_n = sqrt(k/m)` is an exact closed-form result
   for this idealization - this test mainly guards against a units/formula
   regression (e.g. an accidental factor of 2*pi error).
2. Cantilever beam first mode: a real generalized eigenvalue solve
   (`scipy.linalg.eigh(K, M)` on consistent-mass Euler-Bernoulli beam
   matrices) is compared against the closed-form Euler-Bernoulli first
   natural frequency `f1 = (1.875104)^2 / (2*pi*L^2) * sqrt(E*I/(rho*A))`.
   A discretized numerical eigensolve is not exact for this case (unlike
   the structural tip-deflection benchmark), so a tight but non-zero
   relative tolerance is used.
"""
import math

import pytest

from app.module2_simulation.materials import get_property
from app.module2_simulation.schemas import (
    BoundaryConditions,
    Geometry,
    MaterialSelection,
    SimulationCreateRequest,
)
from app.module2_simulation.solvers.modal_solver import ModalSolver

pytestmark = pytest.mark.benchmark


def test_sdof_matches_analytical_frequency():
    mass_kg = 10.0
    stiffness_n_m = 4000.0

    request = SimulationCreateRequest(
        solver_id="modal_eigen_1d_v1",
        material=MaterialSelection(name="steel"),
        geometry=Geometry(dimension="1d"),
        boundary_conditions=BoundaryConditions(point_mass_kg=mass_kg, spring_stiffness_n_m=stiffness_n_m),
    )
    result = ModalSolver().run(request)

    analytical_freq_hz = math.sqrt(stiffness_n_m / mass_kg) / (2 * math.pi)
    assert result.summary_metrics["fundamental_frequency_hz"] == pytest.approx(analytical_freq_hz, rel=1e-9)
    assert result.convergence.converged is True


def test_cantilever_beam_first_mode_matches_analytical():
    length_m = 2.0
    area_m2 = 0.01
    moment_of_inertia_m4 = 8e-6
    elastic_modulus_pa = get_property("steel", "elastic_modulus").value
    density_kg_m3 = get_property("steel", "density").value

    request = SimulationCreateRequest(
        solver_id="modal_eigen_1d_v1",
        material=MaterialSelection(name="steel"),
        geometry=Geometry(
            dimension="1d",
            length_m=length_m,
            cross_section_area_m2=area_m2,
            moment_of_inertia_m4=moment_of_inertia_m4,
            num_elements=15,
        ),
        boundary_conditions=BoundaryConditions(),
    )
    result = ModalSolver().run(request)

    beta1_l = 1.875104
    analytical_freq_hz = (beta1_l**2) / (2 * math.pi * length_m**2) * math.sqrt(
        elastic_modulus_pa * moment_of_inertia_m4 / (density_kg_m3 * area_m2)
    )

    assert result.summary_metrics["fundamental_frequency_hz"] == pytest.approx(analytical_freq_hz, rel=1e-4)
    assert result.convergence.converged is True
