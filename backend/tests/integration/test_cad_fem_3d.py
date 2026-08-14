from __future__ import annotations

import numpy as np
import pytest

from app.module1_design.cad_v2_compiler import compile_design
from app.module2_simulation.cad_fem_solvers import (
    solve_modal_fem_3d, solve_structural_fem_3d, solve_thermal_fem_3d,
)
from app.module2_simulation.fem_core import (
    FEMError, consistent_tet4_mass, isotropic_elasticity_matrix, structural_tet4_matrix,
    tet4_geometry, thermal_tet4_matrix, triangle_convection_matrix,
)
from app.module2_simulation.geometry_physics_schemas import PhysicsModelRequest
from app.module2_simulation.meshing import generate_mesh
from app.module2_simulation.physics_model import PhysicsValidationError, build_physics_model
from tests.integration.test_geometry_physics_foundation import authoritative_box, domain, mesh_spec


pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def mesh():
    return generate_mesh(compile_design(authoritative_box()), [domain()], mesh_spec())


def model(family: str, boundary_conditions: list[dict], settings: dict, outputs: list[str]):
    return PhysicsModelRequest.model_validate({
        "analysis_family": family, "domains": [domain().model_dump(mode="json")],
        "material_assignments": [{"domain_id": "solid_domain", "material_name": "steel"}],
        "boundary_conditions": boundary_conditions, "numerical_settings": settings, "expected_outputs": outputs,
    })


def test_tet4_mathematical_core_is_symmetric_and_mass_positive():
    nodes = np.array(((0., 0., 0.), (1., 0., 0.), (0., 1., 0.), (0., 0., 1.)))
    geometry = tet4_geometry(nodes, (0, 1, 2, 3))
    assert geometry.volume_m3 == pytest.approx(1 / 6)
    assert np.allclose(geometry.gradients_m_inv.sum(axis=0), 0)
    assert np.allclose(thermal_tet4_matrix(geometry, 20), thermal_tet4_matrix(geometry, 20).T)
    stiffness = structural_tet4_matrix(geometry, isotropic_elasticity_matrix(200e9, .3))
    assert np.allclose(stiffness, stiffness.T)
    assert np.allclose(stiffness @ np.tile((1., 0., 0.), 4), 0, atol=1e-4)
    mass = consistent_tet4_mass(geometry, 1000)
    assert np.all(np.linalg.eigvalsh(mass) > 0)
    assert triangle_convection_matrix(.5, 10).sum() == pytest.approx(5)
    with pytest.raises(FEMError):
        tet4_geometry(nodes, (0, 2, 1, 3))


def test_thermal_linear_cube_benchmark(mesh):
    request = model("thermal", [
        {"bc_type": "temperature", "bc_id": "left", "semantic_region": "low_end", "temperature_k": 300},
        {"bc_type": "temperature", "bc_id": "right", "semantic_region": "high_end", "temperature_k": 400},
        {"bc_type": "heat_flux", "bc_id": "insulated", "semantic_region": "walls", "heat_flux_w_m2": 0},
    ], {"settings_type": "steady_thermal"}, ["temperature", "heat_flux"])
    solution = solve_thermal_fem_3d(mesh, build_physics_model(mesh, request))
    temperature = solution.fields["temperature"][1]
    x = np.asarray(mesh.nodes_m)[:, 0]
    expected = 300 + 100 * (x - x.min()) / (x.max() - x.min())
    assert np.max(np.abs(temperature - expected)) < 1e-8
    assert solution.diagnostics["algebraic_residual"] < 1e-10
    assert solution.summary["energy_balance_error"] < 1e-10


def test_structural_axial_prism_benchmark_and_pressure_direction(mesh):
    request = model("structural", [
        {"bc_type": "fixed_support", "bc_id": "fixed", "semantic_region": "low_end"},
        {"bc_type": "force", "bc_id": "traction", "semantic_region": "high_end", "force_n": [100., 0., 0.]},
    ], {"settings_type": "linear_static"}, ["displacement", "stress"])
    solution = solve_structural_fem_3d(mesh, build_physics_model(mesh, request))
    displacement = solution.fields["displacement"][1]
    x = np.asarray(mesh.nodes_m)[:, 0]; end = displacement[np.isclose(x, x.max()), 0]
    expected = 100 * .04 / (200e9 * (.02 * .02))
    assert np.mean(end) == pytest.approx(expected, rel=0.05)
    assert solution.summary["equilibrium_residual"] < 1e-10
    assert solution.diagnostics["algebraic_residual"] < 1e-10

    pressure = model("structural", [
        {"bc_type": "fixed_support", "bc_id": "fixed", "semantic_region": "low_end"},
        {"bc_type": "pressure", "bc_id": "pressure", "semantic_region": "high_end", "pressure_pa": 1e5},
    ], {"settings_type": "linear_static"}, ["displacement", "stress"])
    loaded = solve_structural_fem_3d(mesh, build_physics_model(mesh, pressure))
    assert loaded.fields["displacement"][1][np.isclose(x, x.max()), 0].mean() > 0


def test_modal_constrained_modes_are_mass_normalized_and_refinement_changes_frequency(mesh):
    request = model("modal", [
        {"bc_type": "fixed_support", "bc_id": "fixed", "semantic_region": "low_end"},
    ], {"settings_type": "modal_eigen", "requested_modes": 3}, ["eigenfrequency", "mode_shape"])
    solution = solve_modal_fem_3d(mesh, build_physics_model(mesh, request))
    assert solution.summary["first_natural_frequency_hz"] > 0
    assert solution.diagnostics["maximum_eigenpair_residual"] < 1e-8
    shapes = solution.fields["mode_shapes"][1]
    assert shapes.shape[0] == 3 and np.isfinite(shapes).all()

    refined = generate_mesh(compile_design(authoritative_box()), [domain()], mesh_spec(7.5))
    refined_solution = solve_modal_fem_3d(refined, build_physics_model(refined, request))
    assert refined_solution.summary["first_natural_frequency_hz"] > 0
    assert refined_solution.summary["first_natural_frequency_hz"] != solution.summary["first_natural_frequency_hz"]


def test_underconstrained_structural_and_modal_models_fail(mesh):
    structural = model("structural", [
        {"bc_type": "force", "bc_id": "load", "semantic_region": "high_end", "force_n": [1, 0, 0]},
    ], {"settings_type": "linear_static"}, ["displacement"])
    with pytest.raises(PhysicsValidationError, match="support constraint"):
        build_physics_model(mesh, structural)
    modal = model("modal", [
        {"bc_type": "fixed_support", "bc_id": "fixed", "semantic_region": "low_end"},
    ], {"settings_type": "modal_eigen", "requested_modes": 499}, ["eigenfrequency"])
    with pytest.raises(FEMError, match="fewer"):
        solve_modal_fem_3d(mesh, build_physics_model(mesh, modal))
